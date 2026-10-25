// Developer-only fixture generator; NOT a production or user dependency.
// Run this file from a checked-out official tailscale.com v1.104.2 module
// (commit e26db7a5797683ec99ad1516c40b628c7f2dfd71) with official Go 1.27.3:
//   /path/to/go run /path/to/v2/tests/fixtures/generate_tailscale_1042.go
// Inputs below are deliberately fictional. This never connects to tailscaled,
// a tailnet, a device, or an account. Its output is an offline serialization
// contract fixture, not an actual iPhone whois response or acceptance result.
// Raw output SHA256:
//   7a179b95ceeb411dde97b7979233c5c0e295968bcd7efd6e618592a1743b7b71

package main

import (
  "encoding/json"
  "net/netip"
  "os"
  "runtime"
  "time"
  "tailscale.com/client/tailscale/apitype"
  "tailscale.com/ipn/ipnstate"
  "tailscale.com/tailcfg"
  "tailscale.com/types/key"
)

type scenario struct {
  Name string
  Whois *apitype.WhoIsResponse
  Status *ipnstate.Status
}

func makeCase(name string, authorized bool, expiry time.Time, expired bool) scenario {
  uid := tailcfg.UserID(101)
  profile := tailcfg.UserProfile{ID:uid, LoginName:"fiction@example.invalid", DisplayName:"FICTIONAL CONTRACT USER"}
  ips := []netip.Addr{netip.MustParseAddr("100.101.102.103"),netip.MustParseAddr("fd7a:115c:a1e0::103")}
  node := &tailcfg.Node{ID:202,StableID:"nContractPhone",Name:"fixture-phone.fixture-tail.ts.net.",User:uid,
    Addresses:[]netip.Prefix{netip.PrefixFrom(ips[0],32),netip.PrefixFrom(ips[1],128)},
    MachineAuthorized:authorized,KeyExpiry:expiry,Expired:expired}
  peer := &ipnstate.PeerStatus{ID:node.StableID,NodeID:node.ID,HostName:"localhost",DNSName:node.Name,
    OS:"iOS",UserID:node.User,TailscaleIPs:ips,Online:true,Expired:expired,InNetworkMap:true}
  if !expiry.IsZero(){peer.KeyExpiry=&expiry}
  own := &ipnstate.PeerStatus{ID:"nContractPC",NodeID:201,HostName:"fixture-pc",DNSName:"fixture-pc.fixture-tail.ts.net.",
    OS:"windows",UserID:uid,TailscaleIPs:[]netip.Addr{netip.MustParseAddr("100.101.102.1")},Online:true,InNetworkMap:true}
  return scenario{Name:name,Whois:&apitype.WhoIsResponse{Node:node,UserProfile:&profile},Status:&ipnstate.Status{
    Version:"1.104.2",BackendState:"Running",Self:own,User:map[tailcfg.UserID]tailcfg.UserProfile{uid:profile},
    Peer:map[key.NodePublic]*ipnstate.PeerStatus{key.NodePublic{}:peer},
  }}
}

func main(){
  future := time.Date(2099,1,1,0,0,0,0,time.UTC)
  past := time.Date(2020,1,1,0,0,0,0,time.UTC)
  cases:=[]scenario{makeCase("authorized_nonexpiring",true,time.Time{},false),makeCase("authorized_future",true,future,false),makeCase("unauthorized_omitted",false,time.Time{},false),makeCase("expired",true,past,true)}
  payload:=struct{Origin string; GoVersion string; Cases []scenario}{"Official tailscale v1.104.2 types with fictional inputs; not a real device response",runtime.Version(),cases}
  e:=json.NewEncoder(os.Stdout);e.SetIndent("","  ");if err:=e.Encode(payload);err!=nil{panic(err)}
}
