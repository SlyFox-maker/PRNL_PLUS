package common

import (
	"encoding/binary"
	"net"
	"strings"
	"sync"
)

// TLS inspection is deliberately limited to the client-to-server byte stream.
// It buffers only the initial ClientHello and emits a host once, after the
// complete handshake message is available (even when TCP/relay frames split it).
const wlpMaxHelloBytes = 64 * 1024

type wlpTLSKey struct {
	owner any
	id    uint32
}

type wlpTLSState struct {
	address   string
	record    []byte
	handshake []byte
	done      bool
}

var wlpTLSMu sync.Mutex
var wlpTLSStates = make(map[wlpTLSKey]*wlpTLSState)

func WLPBeginTLSInspection(owner any, id uint32, address string) {
	wlpTLSMu.Lock()
	wlpTLSStates[wlpTLSKey{owner: owner, id: id}] = &wlpTLSState{address: address}
	wlpTLSMu.Unlock()
}

func WLPForgetTLSInspection(owner any, id uint32) {
	wlpTLSMu.Lock()
	delete(wlpTLSStates, wlpTLSKey{owner: owner, id: id})
	wlpTLSMu.Unlock()
}

func WLPInspectClientHello(owner any, id uint32, chunk []byte) string {
	wlpTLSMu.Lock()
	defer wlpTLSMu.Unlock()
	state := wlpTLSStates[wlpTLSKey{owner: owner, id: id}]
	if state == nil || state.done || len(chunk) == 0 {
		return ""
	}
	if len(state.record)+len(chunk)+len(state.handshake) > wlpMaxHelloBytes {
		state.done = true
		state.record = nil
		state.handshake = nil
		return ""
	}
	state.record = append(state.record, chunk...)
	for {
		if len(state.record) == 0 {
			return ""
		}
		if state.record[0] != 22 { // The first client payload is not a TLS handshake.
			state.done = true
			state.record = nil
			return ""
		}
		if len(state.record) < 5 {
			return ""
		}
		recordLen := int(binary.BigEndian.Uint16(state.record[3:5]))
		if recordLen == 0 || recordLen > 18432 {
			state.done = true
			state.record = nil
			return ""
		}
		if len(state.record) < 5+recordLen {
			return ""
		}
		state.handshake = append(state.handshake, state.record[5:5+recordLen]...)
		state.record = state.record[5+recordLen:]
		if len(state.handshake) == 0 {
			continue
		}
		if state.handshake[0] != 1 { // ClientHello
			state.done = true
			return ""
		}
		if len(state.handshake) < 4 {
			continue
		}
		helloLen := int(state.handshake[1])<<16 | int(state.handshake[2])<<8 | int(state.handshake[3])
		if helloLen <= 0 || helloLen+4 > wlpMaxHelloBytes {
			state.done = true
			return ""
		}
		if len(state.handshake) < helloLen+4 {
			continue
		}
		state.done = true
		host := wlpClientHelloSNI(state.handshake[4 : helloLen+4])
		if host == "" {
			return ""
		}
		_, port, err := net.SplitHostPort(state.address)
		if err != nil || port == "" {
			port = "443"
		}
		return net.JoinHostPort(host, port)
	}
}

func wlpClientHelloSNI(hello []byte) string {
	if len(hello) < 34 { // legacy version + random
		return ""
	}
	p := 34
	if p >= len(hello) {
		return ""
	}
	sessionLen := int(hello[p])
	p++
	if p+sessionLen+2 > len(hello) {
		return ""
	}
	p += sessionLen
	cipherLen := int(binary.BigEndian.Uint16(hello[p : p+2]))
	p += 2
	if cipherLen == 0 || cipherLen%2 != 0 || p+cipherLen+1 > len(hello) {
		return ""
	}
	p += cipherLen
	compressionLen := int(hello[p])
	p++
	if compressionLen == 0 || p+compressionLen+2 > len(hello) {
		return ""
	}
	p += compressionLen
	extensionsLen := int(binary.BigEndian.Uint16(hello[p : p+2]))
	p += 2
	if p+extensionsLen > len(hello) {
		return ""
	}
	end := p + extensionsLen
	for p+4 <= end {
		typ := binary.BigEndian.Uint16(hello[p : p+2])
		length := int(binary.BigEndian.Uint16(hello[p+2 : p+4]))
		p += 4
		if p+length > end {
			return ""
		}
		if typ == 0 && length >= 5 {
			listLen := int(binary.BigEndian.Uint16(hello[p : p+2]))
			q, listEnd := p+2, p+2+listLen
			if listEnd > p+length {
				return ""
			}
			for q+3 <= listEnd {
				nameType := hello[q]
				nameLen := int(binary.BigEndian.Uint16(hello[q+1 : q+3]))
				q += 3
				if q+nameLen > listEnd {
					return ""
				}
				if nameType == 0 && nameLen > 0 {
					name := string(hello[q : q+nameLen])
					if strings.IndexFunc(name, func(r rune) bool { return r <= 32 || r >= 127 }) == -1 {
						return strings.TrimSuffix(strings.ToLower(name), ".")
					}
				}
				q += nameLen
			}
		}
		p += length
	}
	return ""
}
