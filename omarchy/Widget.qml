import QtQuick
import Quickshell
import Quickshell.Networking
import qs.Ui
import qs.Commons

BarWidget {
  id: root
  moduleName: "akash.quickwifi"
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  // Shell routing expects this panel-shaped interface. The actual GTK window
  // owns its visibility; summon always presents the single application instance.
  readonly property bool opened: false
  function close() {}

  // Status only. This widget never enables Quickshell's Wi-Fi scanner.
  readonly property var devices: Networking.devices ? Networking.devices.values : []
  readonly property bool connected: {
    for (var i = 0; i < devices.length; i++)
      if (devices[i].type === DeviceType.Wifi && devices[i].connected) return true
    return false
  }

  function open() {
    Quickshell.execDetached([Quickshell.env("HOME") + "/.local/bin/quick-wifi"])
  }
  function toggle() { open() }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.connected ? "󰤨" : "󰤮"
    slotSize: Style.bar.statusSlot
    fontSize: Style.font.caption
    tooltipText: root.connected ? "Quick Wi-Fi · Connected" : "Quick Wi-Fi"
    onPressed: root.open()
  }
}
