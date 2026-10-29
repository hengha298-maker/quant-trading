"""Explicit reviewed future exchange sessions; never infer weekdays as trading.

Only a complete, source-backed, reviewed calendar committed with the program
can extend the existing cutoff. Downloaded drafts cannot activate themselves.
"""
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from urllib.parse import urlsplit
import hashlib,json,re

PATH=Path(__file__).with_name('reviewed_calendars.json')
BASE_THROUGH='2026-12-31'

def validate_year(record,*,now=None,require_reviewed=True):
    now=now or datetime.now(timezone.utc)
    if record.get('exchange')!='XSHG' or type(record.get('year')) is not int or not 2027<=record['year']<=2100:raise ValueError('CALENDAR_REVIEW_IDENTITY_INVALID')
    if require_reviewed and record.get('status')!='reviewed-complete':raise ValueError('CALENDAR_DRAFT_NOT_ACTIVE')
    if not record.get('sources') or not isinstance(record.get('reviewer'),str) or not record['reviewer']:raise ValueError('CALENDAR_REVIEW_EVIDENCE_REQUIRED')
    reviewed=datetime.fromisoformat(record['reviewedAt'])
    if reviewed.tzinfo is None or reviewed>now:raise ValueError('CALENDAR_FUTURE_OR_NAIVE_REVIEW')
    for source in record['sources']:
        u=urlsplit(source['url']);published=datetime.fromisoformat(source['publishedAt'])
        if u.scheme!='https' or u.hostname not in {'www.sse.com.cn','static.sse.com.cn'} or u.username or u.fragment or not re.fullmatch(r'[a-f0-9]{64}',source['sha256']):raise ValueError('CALENDAR_OFFICIAL_SOURCE_REQUIRED')
        if published.tzinfo is None or published>reviewed:raise ValueError('CALENDAR_SOURCE_NOT_AVAILABLE_AT_REVIEW')
    first=date(record['year'],1,1);last=date(record['year'],12,31);expected=[];cursor=first
    while cursor<=last:expected.append(cursor.isoformat());cursor+=timedelta(days=1)
    days=record['days']
    if not isinstance(days,list) or [d.get('date') for d in days]!=expected or any(d.get('status') not in {'open','closed'} or not d.get('basis') for d in days):raise ValueError('CALENDAR_COMPLETE_DAILY_CLASSIFICATION_REQUIRED')
    if not 150<=sum(d['status']=='open' for d in days)<=270:raise ValueError('CALENDAR_SESSION_COUNT_IMPLAUSIBLE')
    if record.get('classificationSha256')!=hashlib.sha256(json.dumps(days,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest():raise ValueError('CALENDAR_CLASSIFICATION_HASH_MISMATCH')
    return record

def reviews(path=None):
    p=Path(path or PATH)
    if p.is_symlink() or p.is_junction():raise ValueError('CALENDAR_REVIEW_REPARSE_REJECTED')
    doc=json.loads(p.read_text(encoding='utf8'))
    if doc.get('version')!=1 or not isinstance(doc.get('calendars'),list):raise ValueError('CALENDAR_REVIEW_SCHEMA_INVALID')
    result={}
    for r in doc['calendars']:
        validate_year(r)
        if r['year'] in result:raise ValueError('CALENDAR_DUPLICATE_YEAR')
        result[r['year']]=r
    return result

def supported_through():
    records=reviews();year=2027
    while year in records:year+=1
    return f'{year-1}-12-31'

def sessions(start,end):
    records=reviews();result=[]
    for year in range(date.fromisoformat(start).year,date.fromisoformat(end).year+1):
        if year not in records:raise ValueError('CALENDAR_RANGE_UNSUPPORTED_AFTER_2026_12_31')
        result.extend(d['date'] for d in records[year]['days'] if start<=d['date']<=end and d['status']=='open')
    return result

def draft(year,sources,days,reviewer):
    """Import explicit classifications for review, not a holiday/weekday guess."""
    return {'exchange':'XSHG','year':year,'status':'draft-not-active','sources':sources,'days':days,'reviewer':reviewer,
      'reviewedAt':datetime.now(timezone.utc).isoformat(),'classificationSha256':hashlib.sha256(json.dumps(days,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()}
