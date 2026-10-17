import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const IFACE_XML = `
<node>
  <interface name="org.agentdockbell.FocusBridge">
    <method name="GetFocusedPID">
      <arg type="u" direction="out" name="pid"/>
      <arg type="s" direction="out" name="wmClass"/>
    </method>
    <method name="ActivateWindow">
      <arg type="u" direction="in" name="pid"/>
      <arg type="b" direction="out" name="activated"/>
    </method>
    <signal name="FocusChanged">
      <arg type="u" name="pid"/>
      <arg type="s" name="wmClass"/>
    </signal>
  </interface>
</node>`;

export default class FocusBridgeExtension extends Extension {
    enable() {
        this._dbusImpl = Gio.DBusExportedObject.wrapJSObject(IFACE_XML, this);
        this._dbusImpl.export(Gio.DBus.session, '/org/agentdockbell/FocusBridge');
        this._nameId = Gio.bus_own_name_on_connection(
            Gio.DBus.session,
            'org.agentdockbell.FocusBridge',
            Gio.BusNameOwnerFlags.REPLACE,
            null,
            null
        );

        this._focusSignalId = global.display.connect('notify::focus-window', () => {
            const [pid, wmClass] = this.GetFocusedPID();
            try {
                this._dbusImpl.emit_signal(
                    'FocusChanged',
                    new GLib.Variant('(us)', [pid, wmClass])
                );
            } catch (_e) {
                // Ignore transient D-Bus emission errors
            }
        });
    }

    disable() {
        if (this._focusSignalId) {
            global.display.disconnect(this._focusSignalId);
            this._focusSignalId = null;
        }
        if (this._nameId) {
            Gio.bus_unown_name(this._nameId);
            this._nameId = null;
        }
        if (this._dbusImpl) {
            this._dbusImpl.unexport();
            this._dbusImpl = null;
        }
    }

    GetFocusedPID() {
        const win = global.display.focus_window;
        if (!win) {
            return [0, ''];
        }
        const pid = win.get_pid ? (win.get_pid() || 0) : 0;
        const wmClass = win.get_wm_class ? (win.get_wm_class() || '') : '';
        return [pid >>> 0, String(wmClass)];
    }

    ActivateWindow(targetPid) {
        const actors = global.get_window_actors ? global.get_window_actors() : [];
        let fallbackWin = null;
        for (const actor of actors) {
            const win = actor.get_meta_window ? actor.get_meta_window() : null;
            if (!win) continue;
            const wPid = win.get_pid ? (win.get_pid() || 0) : 0;
            if (wPid === targetPid) {
                win.activate(global.get_current_time());
                return true;
            }
            const wmClass = (win.get_wm_class ? (win.get_wm_class() || '') : '').toLowerCase();
            if (!fallbackWin && (wmClass.includes('terminal') || wmClass.includes('ptyxis') || wmClass.includes('kitty') || wmClass.includes('alacritty') || wmClass.includes('wezterm') || wmClass.includes('ghostty'))) {
                fallbackWin = win;
            }
        }
        if (fallbackWin) {
            fallbackWin.activate(global.get_current_time());
            return true;
        }
        return false;
    }
}
