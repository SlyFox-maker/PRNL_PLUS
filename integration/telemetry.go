package common

import (
 "encoding/json"
 "fmt"
 "os"
 "sync"
 "sync/atomic"
 "time"
)

// Payload bytes at the relay boundary; excludes WebRTC headers and control/keepalive frames.
var wlpRX, wlpTX atomic.Uint64
var wlpSeen atomic.Int64
var wlpMu sync.Mutex
var wlpConnections func() int

func WLPObserve(in bool, message byte, size int) {
 if in { wlpSeen.Store(time.Now().UnixMilli()) }
 if message != 0x04 && message != 0x06 && message != 0x07 { return }
 if in { wlpRX.Add(uint64(size)) } else { wlpTX.Add(uint64(size)) }
}

func WLPMonitor(connections func() int) {
 wlpMu.Lock()
 wlpConnections = connections
 wlpMu.Unlock()
}

func init() {
 if os.Getenv("WLP_TELEMETRY") != "1" { return }
 go func() {
  for range time.NewTicker(time.Second).C {
   age := int64(-1)
   if seen := wlpSeen.Load(); seen > 0 { age = time.Now().UnixMilli() - seen }
   active := 0
   wlpMu.Lock()
   if wlpConnections != nil { active = wlpConnections() }
   wlpMu.Unlock()
   value, _ := json.Marshal(map[string]any{"rx_bytes":wlpRX.Load(), "tx_bytes":wlpTX.Load(), "peer_age_ms":age, "active_connections":active})
   fmt.Printf("WLP_METRIC:%s\n", value)
  }
 }()
}
