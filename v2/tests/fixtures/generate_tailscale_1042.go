// Developer-only fixture generator; NOT a production or user dependency.
// Run this file from a checked-out official tailscale.com v1.104.2 module
// (commit e26db7a5797683ec99ad1516c40b628c7f2dfd71) with official Go 1.27.3:
//   /path/to/go run /path/to/v2/tests/fixtures/generate_tailscale_1042.go
// Inputs below are deliberately fictional. This never connects to tailscaled,
// a tailnet, a device, or an account. Its output is an offline serialization
// contract fixture, not an actual iPhone whois response or acceptance result.
// MachineAuthorized=false is omitted by the official encoder. Its omission
// alone does not identify whether a fictional input was approved. Cases below
// separately provide or withhold a current network-map peer/public-key binding.
// The deterministic public keys are decoded by the official key.NodePublic
// decoder; no private key is created, read, or needed.

package main

import (
	"encoding/json"
	"net/netip"
	"os"
	"runtime"
	"tailscale.com/client/tailscale/apitype"
	"tailscale.com/ipn/ipnstate"
	"tailscale.com/tailcfg"
	"tailscale.com/types/key"
	"time"
)

type scenario struct {
	Name   string
	Whois  *apitype.WhoIsResponse
	Status *ipnstate.Status
}

func publicKey(text string) key.NodePublic {
	var public key.NodePublic
	if err := public.UnmarshalText([]byte(text)); err != nil {
		panic(err)
	}
	return public
}

func makeCase(name string, authorized bool, expiry time.Time, expired bool) scenario {
	uid := tailcfg.UserID(101)
	profile := tailcfg.UserProfile{ID: uid, LoginName: "fiction@example.invalid", DisplayName: "FICTIONAL CONTRACT USER"}
	ips := []netip.Addr{netip.MustParseAddr("100.101.102.103"), netip.MustParseAddr("fd7a:115c:a1e0::103")}
	public := publicKey("nodekey:1111111111111111111111111111111111111111111111111111111111111111")
	node := &tailcfg.Node{ID: 202, StableID: "nContractPhone", Name: "fixture-phone.fixture-tail.ts.net.", User: uid, Key: public,
		Addresses:         []netip.Prefix{netip.PrefixFrom(ips[0], 32), netip.PrefixFrom(ips[1], 128)},
		MachineAuthorized: authorized, KeyExpiry: expiry, Expired: expired}
	peer := &ipnstate.PeerStatus{ID: node.StableID, NodeID: node.ID, PublicKey: public, HostName: "localhost", DNSName: node.Name,
		OS: "iOS", UserID: node.User, TailscaleIPs: ips, Online: true, Expired: expired, InNetworkMap: true}
	if !expiry.IsZero() {
		peer.KeyExpiry = &expiry
	}
	own := &ipnstate.PeerStatus{ID: "nContractPC", NodeID: 201, PublicKey: publicKey("nodekey:2222222222222222222222222222222222222222222222222222222222222222"), HostName: "fixture-pc", DNSName: "fixture-pc.fixture-tail.ts.net.",
		OS: "windows", UserID: uid, TailscaleIPs: []netip.Addr{netip.MustParseAddr("100.101.102.1")}, Online: true, InNetworkMap: true}
	return scenario{Name: name, Whois: &apitype.WhoIsResponse{Node: node, UserProfile: &profile}, Status: &ipnstate.Status{
		Version: "1.104.2", BackendState: "Running", Self: own, User: map[tailcfg.UserID]tailcfg.UserProfile{uid: profile},
		Peer: map[key.NodePublic]*ipnstate.PeerStatus{public: peer},
	}}
}

func main() {
	future := time.Date(2099, 1, 1, 0, 0, 0, 0, time.UTC)
	past := time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC)
	omittedWithPeer := makeCase("authorized_peer_omitted", false, time.Time{}, false)
	// This scenario explicitly lacks any approved peer key in the current map;
	// it does NOT claim that omission of the boolean by itself means unapproved.
	omittedWithoutPeer := makeCase("unauthorized_omitted", false, time.Time{}, false)
	omittedWithoutPeer.Status.Peer = map[key.NodePublic]*ipnstate.PeerStatus{}
	omittedZeroKey := makeCase("zero_key_peer_omitted", false, time.Time{}, false)
	for _, peer := range omittedZeroKey.Status.Peer {
		peer.PublicKey = key.NodePublic{}
		omittedZeroKey.Status.Peer = map[key.NodePublic]*ipnstate.PeerStatus{key.NodePublic{}: peer}
		break
	}
	omittedZeroKey.Whois.Node.Key = key.NodePublic{}
	cases := []scenario{makeCase("authorized_nonexpiring", true, time.Time{}, false), makeCase("authorized_future", true, future, false), omittedWithPeer, omittedWithoutPeer, omittedZeroKey, makeCase("expired", true, past, true)}
	payload := struct {
		Origin    string
		GoVersion string
		Cases     []scenario
	}{"Official tailscale v1.104.2 types with fictional inputs; not a real device response", runtime.Version(), cases}
	e := json.NewEncoder(os.Stdout)
	e.SetIndent("", "  ")
	if err := e.Encode(payload); err != nil {
		panic(err)
	}
}
