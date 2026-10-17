"""Patch only the pinned build copy; fail if upstream hooks have drifted."""
from pathlib import Path
import shutil
import sys

root = Path(sys.argv[1])
shutil.copyfile(Path(__file__).with_name('telemetry.go'), root / 'relay/common/wlp_telemetry.go')
shutil.copyfile(Path(__file__).with_name('tls_sni.go'), root / 'relay/common/wlp_tls_sni.go')

def replace(file, before, after):
    p = root / file
    value = p.read_text()
    assert value.count(before) == 1, f'Upstream hook changed: {file}: {before}'
    p.write_text(value.replace(before, after))

replace('relay/tunnel/relay_bridge.go', '\ttunnel.SetOnData(rb.handleTunnelData)',
        '\tcommon.WLPMonitor(func() int { tcp, udp, _ := rb.Stats(); return tcp + udp })\n\ttunnel.SetOnData(rb.handleTunnelData)')
# Emit one machine-readable destination event per relay connection. The initial
# SOCKS destination is recorded immediately, then replaced with TLS SNI when a
# fragmented ClientHello is fully available from the client-to-server stream.
replace('relay/tunnel/relay_bridge.go', '\trb.logFn("relay: CONNECTED %d -> %s", connID, common.MaskAddr(addr))',
        '\trb.logFn("WLP_DESTINATION\\t%d\\t%s", connID, addr)\n\trb.logFn("relay: CONNECTED %d -> %s", connID, common.MaskAddr(addr))')
replace('relay/tunnel/relay_bridge.go', '\trb.conns.Store(connID, conn)\n\trb.send(connID, MsgConnectOK, nil)',
        '\trb.conns.Store(connID, conn)\n\tcommon.WLPBeginTLSInspection(rb, connID, addr)\n\trb.send(connID, MsgConnectOK, nil)')
replace('relay/tunnel/relay_bridge.go', '\t\tif c, ok := val.(net.Conn); ok {\n\t\t\tif _, err := c.Write(payload); err != nil {',
        '\t\tif c, ok := val.(net.Conn); ok {\n\t\t\tif sni := common.WLPInspectClientHello(rb, connID, payload); sni != "" { rb.logFn("WLP_DESTINATION\\t%d\\t%s", connID, sni) }\n\t\t\tif _, err := c.Write(payload); err != nil {')
replace('relay/tunnel/relay_bridge.go', '\tcase MsgClose:\n\t\tfound := false',
        '\tcase MsgClose:\n\t\tcommon.WLPForgetTLSInspection(rb, connID)\n\t\tfound := false')
replace('relay/tunnel/relay_bridge.go', '\trb.send(connID, MsgClose, nil)\n\trb.conns.Delete(connID)',
        '\tcommon.WLPForgetTLSInspection(rb, connID)\n\trb.send(connID, MsgClose, nil)\n\trb.conns.Delete(connID)')
replace('relay/tunnel/relay_bridge.go', '\tframe := EncodeFrame(connID, msgType, payload)',
        '\tcommon.WLPObserve(false, msgType, len(payload))\n\tframe := EncodeFrame(connID, msgType, payload)')
replace('relay/tunnel/relay_bridge.go', '\tDecodeFrames(data, func(connID uint32, msgType byte, payload []byte) {',
        '\tDecodeFrames(data, func(connID uint32, msgType byte, payload []byte) {\n\t\tcommon.WLPObserve(true, msgType, len(payload))')
replace('headless/vk/tunnel_relay.go', '\tpayload := data[5:]',
        '\tpayload := data[5:]\n\tcommon.WLPObserve(true, mt, len(payload))\n\tcommon.WLPMonitor(func() int { count := 0; u.conns.Range(func(_, _ any) bool { count++; return true }); return count })')
replace('headless/vk/tunnel_relay.go', '\tu.dc.Send(wire)',
        '\tif err := u.dc.Send(wire); err == nil { common.WLPObserve(false, mt, len(payload)) }')

# Add an authenticated loopback bridge so the real Electron Creator window and
# its persistent Electron cookie store can be driven from the control panel.
shutil.copyfile(Path(__file__).with_name('desktop_control.ts'), root / 'creator-app/src/main/desktop-control.ts')
replace('creator-app/src/main/index.ts', "import { registerIpcHandlers } from './ipc';",
        "import { registerIpcHandlers } from './ipc';\nimport { startDesktopControl } from './desktop-control';\nimport { mkdirSync } from 'fs';")
replace('creator-app/src/main/index.ts', 'const tabManager = new TabManager();',
        "const creatorUserData = process.env.WLP_CREATOR_USER_DATA_DIR;\nif (creatorUserData) { mkdirSync(creatorUserData, { recursive: true }); app.setPath('userData', creatorUserData); }\n\nconst tabManager = new TabManager();")
replace('creator-app/src/main/index.ts', '  tabManager.mainWindow = win;',
        "  tabManager.mainWindow = win;\n  const controlPort = Number(process.env.WLP_CREATOR_CONTROL_PORT || 0);\n  const controlToken = process.env.WLP_CREATOR_CONTROL_TOKEN || '';\n  if (controlPort > 0 && controlToken) startDesktopControl(win, tabManager, controlPort, controlToken);")
replace('creator-app/src/renderer/index.ts', '\ninit();',
        "\ninit();\nif (process.env.WLP_DESKTOP_CREATOR_TELEMOST === '1') {\n  setTimeout(() => { tm.createTab(); loadURL(tm, TELEMOST_URL); }, 350);\n}")
