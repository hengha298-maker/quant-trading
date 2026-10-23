"""Execute the actual, verified PR #3 sources; no copied validators or pyc reuse."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

P1_COMMIT = 'f6c117fab964f2e627d1a64a651bab2827ff0308'
FILES = {
    '__init__.py': '0964fa89346cab7c73f2dcb2d50f91f28e1248bdf570b70faf5e6376784227e5',
    'validation.py': '4550cc31857f95fd55057059b35637866f828bf812f38ae79e59cd3e37dc9d5e',
    'usage.py': '8efc0c899c2e5f42269979d6c52b97992deb06bebf07f5542e60c2347e75d5e8',
    'report_model.py': 'c3e1ebbdc3751139ea15e4f1ea4345f25eb6fb8a4330a36c66de777c09cd8feb',
}
PACKAGE = '_goose_p1_f6c117f'
LOCK = threading.RLock()


def load_p1(code_directory):
    try:
        with LOCK:
            root = Path(code_directory).resolve(strict=True)
            if (root / 'p1').is_dir():
                root = (root / 'p1').resolve(strict=True)
            package = root / 'goose_research'
            sources = {}
            for name, digest in FILES.items():
                source = package / name
                if source.is_symlink() or source.resolve(strict=True) != source.absolute():
                    raise ValueError()
                with source.open('rb') as stream:
                    raw = stream.read(200_001)
                if len(raw)>200_000 or hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()!=digest:
                    raise ValueError()
                sources[name]=raw
            # Relative imports resolve ONLY the four already verified modules.
            # Compile the checked buffers directly: stale/untrusted __pycache__
            # and an unrelated installed goose_research cannot be executed.
            modules = {}
            for file, raw in sources.items():
                name = PACKAGE if file=='__init__.py' else PACKAGE+'.'+file[:-3]
                spec = importlib.util.spec_from_file_location(name,package/file,
                    submodule_search_locations=[] if file=='__init__.py' else None)
                module = importlib.util.module_from_spec(spec)
                sys.modules[name]=module
                exec(compile(raw,str(package/file),'exec'),module.__dict__)
                modules[file]=module
            return SimpleNamespace(validation=modules['validation.py'],usage=modules['usage.py'],
                                   model=modules['report_model.py'],revision=P1_COMMIT)
    except (OSError,ValueError,ImportError,AttributeError,TypeError):
        raise ValueError('P1源码缺失或核心版本不匹配；请选择指定PR #3源码。不会降级或绕过校验。') from None
