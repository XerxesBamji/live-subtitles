#!/usr/bin/env python3
"""
Live Subtitles – a small control window for the live Spanish -> English translator.

Runs live_server.py in the background and lets you:
  • see whether it's ready / translating, plus the latest translated line
  • switch model (Small / Medium / Large)
  • restart it, open its log, start the app at login, quit

Closing the window keeps the translator running; click the Dock icon (or the
menu bar icon, if visible) to bring the window back. Quit stops everything.
"""
import fcntl
import os
import plistlib
import re
import subprocess
import sys
import threading

import objc
from AppKit import (
    NSApp, NSApplication, NSApplicationActivationPolicyRegular, NSBackingStoreBuffered,
    NSBox, NSBundle, NSButton, NSColor, NSDistributedNotificationCenter, NSFont, NSImage,
    NSMakeRect, NSMenu, NSMenuItem, NSPopUpButton, NSStatusBar, NSTextField,
    NSVariableStatusItemLength, NSWindow, NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskTitled,
)
from Foundation import NSObject, NSTimer
from PyObjCTools import AppHelper

import desktop_capture as dc

# Show "Live Subtitles" (not "Python") in the app menu.
NSBundle.mainBundle().infoDictionary()["CFBundleName"] = "Live Subtitles"

HERE = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(HERE, ".venv", "bin", "python")
SERVER = os.path.join(HERE, "live_server.py")
SERVER_LOG = os.path.join(HERE, "server_output.log")
PREFS_PATH = os.path.join(HERE, ".app_prefs")
LOCK_PATH = os.path.join(HERE, ".app.lock")
APP_PATH = os.path.expanduser("~/Applications/Live Subtitles.app")
ICON_PATH = os.path.join(APP_PATH, "Contents", "Resources", "AppIcon.icns")
AGENT_LABEL = "com.livesubtitles.app"
AGENT_PATH = os.path.expanduser(f"~/Library/LaunchAgents/{AGENT_LABEL}.plist")
SHOW_NOTIFICATION = "com.livesubtitles.show"

MODELS = [("small", "Small – fastest"), ("medium", "Medium – balanced"), ("large", "Large – most accurate")]

GREEN = NSColor.systemGreenColor()
RED = NSColor.systemRedColor()
ORANGE = NSColor.systemOrangeColor()
GRAY = NSColor.secondaryLabelColor()


def load_model_pref() -> str:
    try:
        m = open(PREFS_PATH).read().strip()
        if m in dict(MODELS):
            return m
    except OSError:
        pass
    return "medium"


def label(text, frame, size=13, bold=False, color=None, wrap=False):
    f = NSTextField.wrappingLabelWithString_(text) if wrap else NSTextField.labelWithString_(text)
    f.setFrame_(frame)
    f.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
    if color is not None:
        f.setTextColor_(color)
    return f


class Controller(NSObject):
    def init(self):
        self = objc.super(Controller, self).init()
        self.proc = None
        self.model = load_model_pref()
        self.connections = 0
        self.pending = {}          # UI updates queued by the watcher thread
        self.lock = threading.Lock()
        self.desktop = dc.DesktopSubtitles(self.set_desktop_status)
        return self

    # ---- app lifecycle --------------------------------------------------
    def applicationDidFinishLaunching_(self, _):
        if os.path.exists(ICON_PATH):
            NSApp.setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(ICON_PATH))
        self.build_main_menu()
        self.build_window()
        self.build_status_item()
        NSDistributedNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self, "showWindow:", SHOW_NOTIFICATION, None)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.3, self, "tick:", None, True)
        self.start_server()
        self.showWindow_(None)

    def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _flag):
        self.showWindow_(None)  # Dock icon clicked
        return True

    def applicationShouldTerminateAfterLastWindowClosed_(self, _app):
        return False  # keep translating with the window closed

    def applicationWillTerminate_(self, _):
        self.desktop.stop()
        self.stop_server()

    # ---- UI -------------------------------------------------------------
    def build_main_menu(self):
        bar = NSMenu.alloc().init()
        app_item = NSMenuItem.alloc().init()
        bar.addItem_(app_item)
        app_menu = NSMenu.alloc().init()
        app_menu.addItemWithTitle_action_keyEquivalent_("Hide Window", "performClose:", "w")
        app_menu.addItemWithTitle_action_keyEquivalent_("Quit Live Subtitles", "terminate:", "q")
        app_item.setSubmenu_(app_menu)
        NSApp.setMainMenu_(bar)

    def build_window(self):
        W, H = 400, 430
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable,
            NSBackingStoreBuffered, False)
        self.window.setTitle_("Live Subtitles")
        self.window.setReleasedWhenClosed_(False)
        self.window.center()
        v = self.window.contentView()

        v.addSubview_(label("Spanish → English live subtitles", NSMakeRect(20, H - 40, W - 40, 22), 15, bold=True))

        self.dot = label("●", NSMakeRect(20, H - 70, 18, 20), 15, color=ORANGE)
        self.status = label("Starting…", NSMakeRect(40, H - 70, W - 60, 20), 13)
        v.addSubview_(self.dot)
        v.addSubview_(self.status)

        box = NSBox.alloc().initWithFrame_(NSMakeRect(16, H - 150, W - 32, 72))
        box.setTitle_("Latest line")
        box.setTitleFont_(NSFont.systemFontOfSize_(11))
        self.last_line = label("—", NSMakeRect(8, 4, W - 64, 40), 12, color=GRAY, wrap=True)
        box.contentView().addSubview_(self.last_line)
        v.addSubview_(box)

        # ---- Desktop subtitles (Safari or any app) ----
        sep = NSBox.alloc().initWithFrame_(NSMakeRect(16, H - 166, W - 32, 1))
        sep.setBoxType_(2)  # separator line
        v.addSubview_(sep)
        v.addSubview_(label("Floating subtitles (Safari, Chrome, any app)", NSMakeRect(20, H - 196, W - 40, 20), 13, bold=True))
        v.addSubview_(label("Listen to", NSMakeRect(20, H - 228, 70, 20)))
        self.source_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(90, H - 234, 210, 28), False)
        self.source_popup.addItemsWithTitles_([t for _, t in dc.SOURCES])
        v.addSubview_(self.source_popup)
        self.desktop_btn = NSButton.buttonWithTitle_target_action_("Start", self, "toggleDesktop:")
        self.desktop_btn.setFrame_(NSMakeRect(304, H - 236, 80, 32))
        v.addSubview_(self.desktop_btn)
        self.lock_box = NSButton.checkboxWithTitle_target_action_(
            "Lock subtitle position (untick to drag / resize it)", self, "toggleLock:")
        self.lock_box.setFrame_(NSMakeRect(18, H - 262, W - 36, 22))
        self.lock_box.setState_(1)
        v.addSubview_(self.lock_box)
        self.desktop_status = label("Off – use this for Safari or other apps", NSMakeRect(20, H - 288, W - 40, 18), 11, color=GRAY)
        v.addSubview_(self.desktop_status)
        sep2 = NSBox.alloc().initWithFrame_(NSMakeRect(16, H - 300, W - 32, 1))
        sep2.setBoxType_(2)
        v.addSubview_(sep2)

        v.addSubview_(label("Model", NSMakeRect(20, 98, 60, 20)))
        self.popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(80, 92, 220, 28), False)
        self.popup.addItemsWithTitles_([t for _, t in MODELS])
        self.popup.selectItemAtIndex_([m for m, _ in MODELS].index(self.model))
        self.popup.setTarget_(self)
        self.popup.setAction_("modelChanged:")
        v.addSubview_(self.popup)

        self.login_box = NSButton.checkboxWithTitle_target_action_("Open at login", self, "toggleLogin:")
        self.login_box.setFrame_(NSMakeRect(18, 60, 200, 22))
        self.login_box.setState_(1 if os.path.exists(AGENT_PATH) else 0)
        v.addSubview_(self.login_box)

        for i, (title, action) in enumerate(
                [("Restart", "restart:"), ("Open Log", "openLog:"), ("Quit", "quit:")]):
            b = NSButton.buttonWithTitle_target_action_(title, self, action)
            b.setFrame_(NSMakeRect(16 + i * 124, 14, 120, 32))
            v.addSubview_(b)

    def build_status_item(self):
        # Optional extra: may be hidden behind the notch on crowded menu bars.
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self.status_item.button().setTitle_("⏳")
        menu = NSMenu.alloc().init()
        for title, action in [("Show Window", "showWindow:"), ("Restart Translator", "restart:"),
                              (None, None), ("Quit Live Subtitles", "quit:")]:
            if title is None:
                menu.addItem_(NSMenuItem.separatorItem())
            else:
                item = menu.addItemWithTitle_action_keyEquivalent_(title, action, "")
                item.setTarget_(self)
        self.status_item.setMenu_(menu)

    @objc.python_method
    def set_state(self, icon, color, text):
        with self.lock:
            self.pending["state"] = (icon, color, text)

    @objc.python_method
    def set_last_line(self, text):
        with self.lock:
            self.pending["line"] = text

    def tick_(self, _):
        with self.lock:
            pending, self.pending = self.pending, {}
        if "state" in pending:
            icon, color, text = pending["state"]
            self.dot.setTextColor_(color)
            self.status.setStringValue_(text)
            self.status_item.button().setTitle_(icon)
        if "line" in pending:
            self.last_line.setStringValue_(pending["line"])
        if "desktop" in pending:
            self.desktop_status.setStringValue_(pending["desktop"])
            if not self.desktop.running:
                self.desktop_btn.setTitle_("Start")
        try:
            self.desktop.tick()
        except Exception:
            import traceback
            with open(os.path.join(HERE, "desktop_errors.log"), "a") as f:
                traceback.print_exc(file=f)

    @objc.python_method
    def set_desktop_status(self, text):
        with self.lock:
            self.pending["desktop"] = text

    # ---- actions --------------------------------------------------------
    def toggleDesktop_(self, sender):
        if self.desktop.running:
            self.desktop.stop()
            sender.setTitle_("Start")
            self.set_desktop_status("Off")
            return
        if not dc.has_permission():
            dc.request_permission()
            subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"])
            self.set_desktop_status("Allow this app under Screen & System Audio Recording, then Quit and reopen it")
            return
        bundle_id = dc.SOURCES[self.source_popup.indexOfSelectedItem()][0]
        self.desktop.start(bundle_id)
        sender.setTitle_("Stop")
        self.set_desktop_status("Starting…")

    def toggleLock_(self, sender):
        self.desktop.set_locked(bool(sender.state()))

    def showWindow_(self, _):
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    def modelChanged_(self, sender):
        model = MODELS[sender.indexOfSelectedItem()][0]
        if model != self.model:
            self.model = model
            open(PREFS_PATH, "w").write(model)
            self.restart_(None)

    def restart_(self, _):
        self.stop_server()
        self.start_server()

    def openLog_(self, _):
        subprocess.run(["open", "-a", "Console", SERVER_LOG])

    def toggleLogin_(self, sender):
        if sender.state():
            os.makedirs(os.path.dirname(AGENT_PATH), exist_ok=True)
            with open(AGENT_PATH, "wb") as f:
                plistlib.dump({"Label": AGENT_LABEL,
                               "ProgramArguments": ["/usr/bin/open", APP_PATH],
                               "RunAtLoad": True}, f)
        elif os.path.exists(AGENT_PATH):
            os.remove(AGENT_PATH)

    def quit_(self, _):
        NSApp.terminate_(None)

    # ---- translator process ---------------------------------------------
    def start_server(self):
        self.connections = 0
        self.set_state("⏳", ORANGE, f"Loading {self.model} model…")
        env = dict(os.environ, PYTHONWARNINGS="ignore", PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(
            [PYTHON, SERVER, "--model", self.model, "--exit-with-parent"],
            cwd=HERE, env=env, text=True, bufsize=1,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        threading.Thread(target=self.watch, args=(self.proc,), daemon=True).start()

    def stop_server(self):
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    @objc.python_method
    def watch(self, proc):
        ready = ("💬", GREEN, "Ready – click the extension button on your YouTube tab")
        with open(SERVER_LOG, "w") as log:
            for line in proc.stdout:
                log.write(line)
                log.flush()
                if proc is not self.proc:
                    continue
                m = re.match(r"\[\s*[\d.]+s\] (.*)", line)
                if m:
                    self.set_last_line(m.group(1).strip())
                elif "Ready on" in line:
                    self.set_state(*ready)
                elif "Browser connected" in line:
                    self.connections += 1
                    self.set_state("🔴", RED, "Translating…")
                elif "Browser disconnected" in line:
                    self.connections = max(0, self.connections - 1)
                    if not self.connections:
                        self.set_state(*ready)
                elif "address already in use" in line.lower():
                    self.set_state("⚠️", ORANGE, "Port in use – is ./live.sh running in Terminal?")
        if proc is self.proc and proc.wait() != 0:
            self.set_state("⚠️", ORANGE, "Translator stopped – click Restart or Open Log")


def main():
    # Only one copy: if already running, just bring its window forward.
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        NSDistributedNotificationCenter.defaultCenter() \
            .postNotificationName_object_userInfo_deliverImmediately_(SHOW_NOTIFICATION, None, None, True)
        sys.exit(0)

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = Controller.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()

