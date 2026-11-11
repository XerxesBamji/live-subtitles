"""
Desktop subtitles: capture an app's audio (Safari, Chrome, or everything) with
ScreenCaptureKit, stream it to the local translator, and show the English
subtitles in a floating, click-through window that sits above everything –
including fullscreen video.

Used by app.py. Needs Screen Recording permission (macOS asks once).
"""
import json
import queue
import threading
import time

import numpy as np
import objc
import CoreMedia as CM
import ScreenCaptureKit as SCK
import Quartz
from AppKit import (
    NSBackingStoreBuffered, NSColor, NSFont, NSMakeRect, NSPanel, NSScreen, NSTextField,
    NSView, NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary, NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
    NSWindowStyleMaskResizable, NSStatusWindowLevel, NSLineBreakByWordWrapping,
    NSTextAlignmentCenter,
)
from websockets.sync.client import connect

import os
SERVER = os.environ.get("LIVE_SUBS_SERVER", "ws://localhost:8765")
SR = 16000

SOURCES = [
    ("com.apple.Safari", "Safari"),
    ("com.google.Chrome", "Google Chrome"),
    (None, "All system audio"),
]

# Same colours as the Chrome overlay (white, yellow, cyan, green, pink, orange).
SPEAKER_RGB = [(1, 1, 1), (1, 0.88, 0.4), (0.5, 0.86, 1), (0.61, 1, 0.61), (1, 0.68, 0.84), (1, 0.7, 0.4)]
MAX_CHARS = 90


def has_permission() -> bool:
    return bool(Quartz.CGPreflightScreenCaptureAccess())


def request_permission():
    Quartz.CGRequestScreenCaptureAccess()


def tail(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    cut = text[-MAX_CHARS:]
    sp = cut.find(" ")
    return "…" + (cut[sp + 1:] if sp >= 0 else cut)


# --------------------------------------------------------------------------
# Audio capture
# --------------------------------------------------------------------------
StreamOutput = objc.protocolNamed("SCStreamOutput")
StreamDelegate = objc.protocolNamed("SCStreamDelegate")


class _Output(Quartz.NSObject, protocols=[StreamOutput, StreamDelegate]):
    def initWithCallback_(self, cb):
        self = objc.super(_Output, self).init()
        self.cb = cb
        self.logged_format = False
        return self

    def stream_didOutputSampleBuffer_ofType_(self, stream, sbuf, kind):
        if kind != SCK.SCStreamOutputTypeAudio:
            return
        try:
            bb = CM.CMSampleBufferGetDataBuffer(sbuf)
            if bb is None:
                return
            n = CM.CMBlockBufferGetDataLength(bb)
            res = CM.CMBlockBufferCopyDataBytes(bb, 0, n, None)
            data = res[1] if isinstance(res, tuple) else res
            pcm = np.frombuffer(bytes(data), dtype=np.float32)
            if not self.logged_format:
                self.logged_format = True
                fmt = CM.CMSampleBufferGetFormatDescription(sbuf)
                asbd = CM.CMAudioFormatDescriptionGetStreamBasicDescription(fmt)
                print(f"[capture] audio format: {asbd}", flush=True)
            self.cb(pcm)
        except Exception as e:  # never let an exception escape into ScreenCaptureKit
            print(f"[capture] error reading audio: {e!r}", flush=True)

    def stream_didStopWithError_(self, stream, error):
        print(f"[capture] stream stopped: {error}", flush=True)


class AudioCapture:
    """Captures 16 kHz mono float32 audio from one app (or all apps)."""

    def __init__(self, bundle_id, on_audio, on_error):
        self.bundle_id = bundle_id
        self.on_audio = on_audio
        self.on_error = on_error
        self.stream = None
        self.output = _Output.alloc().initWithCallback_(on_audio)

    def start(self):
        SCK.SCShareableContent.getShareableContentWithCompletionHandler_(self._got_content)

    def _got_content(self, content, error):
        if error is not None or content is None:
            self.on_error(f"Can't capture audio – allow Screen Recording for this app ({error})")
            return
        displays = content.displays()
        if not displays:
            self.on_error("No display found for capture")
            return
        if self.bundle_id:
            apps = [a for a in content.applications() if a.bundleIdentifier() == self.bundle_id]
            if not apps:
                self.on_error("That browser isn't open – open it first, then press Start")
                return
            filt = SCK.SCContentFilter.alloc().initWithDisplay_includingApplications_exceptingWindows_(
                displays[0], apps, [])
        else:
            filt = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(displays[0], [])

        cfg = SCK.SCStreamConfiguration.alloc().init()
        cfg.setCapturesAudio_(True)
        cfg.setSampleRate_(SR)
        cfg.setChannelCount_(1)
        cfg.setExcludesCurrentProcessAudio_(True)
        # We only want audio: keep the (mandatory) video stream tiny and slow.
        cfg.setWidth_(2)
        cfg.setHeight_(2)
        cfg.setMinimumFrameInterval_(CM.CMTimeMake(1, 1))

        self.stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(filt, cfg, self.output)
        for kind in (SCK.SCStreamOutputTypeAudio, SCK.SCStreamOutputTypeScreen):
            ok, err = self.stream.addStreamOutput_type_sampleHandlerQueue_error_(self.output, kind, None, None)
            if not ok:
                self.on_error(f"Couldn't attach audio output: {err}")
                return
        self.stream.startCaptureWithCompletionHandler_(self._started)

    def _started(self, error):
        if error is not None:
            self.on_error(f"Can't capture audio – allow Screen Recording for this app ({error})")
        else:
            print("[capture] started", flush=True)

    def stop(self):
        if self.stream is not None:
            self.stream.stopCaptureWithCompletionHandler_(lambda err: None)
            self.stream = None


# --------------------------------------------------------------------------
# Connection to the translator
# --------------------------------------------------------------------------
class TranslatorClient:
    """Sends audio to live_server.py and receives captions (same protocol as the extension)."""

    def __init__(self, on_caption, on_status):
        self.on_caption = on_caption
        self.on_status = on_status
        self.q = queue.Queue(maxsize=200)
        self.running = False

    def start(self):
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.running = False

    def push(self, pcm: np.ndarray):
        try:
            self.q.put_nowait(pcm.astype(np.float32).tobytes())
        except queue.Full:
            pass  # translator not connected: drop audio rather than lag

    def _run(self):
        while self.running:
            try:
                with connect(SERVER, max_size=None) as ws:
                    self.on_status("connected")
                    threading.Thread(target=self._recv, args=(ws,), daemon=True).start()
                    while self.running:
                        try:
                            ws.send(self.q.get(timeout=0.5))
                        except queue.Empty:
                            continue
            except Exception:
                self.on_status("waiting")
                time.sleep(2)

    def _recv(self, ws):
        try:
            for msg in ws:
                m = json.loads(msg)
                self.on_caption(m.get("type"), m.get("text", ""), m.get("speaker"))
        except Exception:
            pass


# --------------------------------------------------------------------------
# Floating subtitle window
# --------------------------------------------------------------------------
class SubtitleWindow:
    """Borderless, click-through panel above all windows (and fullscreen video)."""

    def __init__(self, font_size=24):
        screen = NSScreen.mainScreen().frame()
        w, h = min(900, screen.size.width * 0.7), 120
        x = (screen.size.width - w) / 2
        y = screen.size.height * 0.10
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, w, h),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered, False)
        self.panel.setLevel_(NSStatusWindowLevel)
        self.panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(False)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setMovableByWindowBackground_(True)
        self.panel.setFrameAutosaveName_("LiveSubtitlesOverlay")  # remembers position/size

        content = self.panel.contentView()
        content.setWantsLayer_(True)

        self.bubble = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        self.bubble.setWantsLayer_(True)
        self.bubble.layer().setBackgroundColor_(NSColor.colorWithWhite_alpha_(0, 0.72).CGColor())
        self.bubble.layer().setCornerRadius_(8)
        content.addSubview_(self.bubble)

        self.label = NSTextField.wrappingLabelWithString_("")
        self.label.setFont_(NSFont.systemFontOfSize_weight_(font_size, 0.3))
        self.label.setAlignment_(NSTextAlignmentCenter)
        self.label.setMaximumNumberOfLines_(2)
        self.label.cell().setLineBreakMode_(NSLineBreakByWordWrapping)
        self.label.setTextColor_(NSColor.whiteColor())
        self.bubble.addSubview_(self.label)

        self.final_text, self.final_spk, self.prev_spk = "", None, None
        self.partial_text, self.partial_spk = "", None
        self.last_update = 0.0
        self.locked = True
        self.set_locked(True)

    # -- positioning ------------------------------------------------------
    def set_locked(self, locked: bool):
        """Locked = click-through and invisible when silent; unlocked = drag/resize it."""
        self.locked = locked
        self.panel.setIgnoresMouseEvents_(locked)
        if not locked:
            self._show("Drag me where you want subtitles · resize from the edges · then tick “Lock” in the app",
                       NSColor.whiteColor())
        else:
            self.render()

    def show(self):
        self.panel.orderFrontRegardless()

    def hide(self):
        self.panel.orderOut_(None)

    # -- captions ---------------------------------------------------------
    def caption(self, kind, text, speaker):
        if kind == "partial":
            self.partial_text, self.partial_spk = text, speaker if speaker is not None else self.final_spk
        elif kind == "final":
            self.partial_text = ""
            if text:
                self.prev_spk = self.final_spk if self.final_text else None
                self.final_text = text
                if speaker is not None:
                    self.final_spk = speaker
            else:
                return  # laughter/noise: don't re-show an old line
        elif kind == "status":
            self.partial_text, self.partial_spk = text, None
        self.last_update = time.monotonic()
        self.render()

    def tick(self):
        """Call regularly from the main thread: hides stale subtitles."""
        if self.locked and (self.final_text or self.partial_text) and time.monotonic() - self.last_update > 4:
            self.final_text = self.partial_text = ""
            self.prev_spk = None
            self.render()

    def render(self):
        if not self.locked:
            return
        is_partial = bool(self.partial_text)
        text = tail(self.partial_text if is_partial else self.final_text)
        spk = self.partial_spk if is_partial else self.final_spk
        if text and not is_partial and spk is not None and self.prev_spk is not None and spk != self.prev_spk:
            text = "– " + text
        r, g, b = SPEAKER_RGB[spk % len(SPEAKER_RGB)] if spk is not None else SPEAKER_RGB[0]
        color = NSColor.colorWithRed_green_blue_alpha_(r, g, b, 0.85 if is_partial else 1.0)
        if text:
            self._show(text, color)
        else:
            self.bubble.setHidden_(True)

    def _show(self, text, color):
        self.label.setStringValue_(text)
        self.label.setTextColor_(color)
        frame = self.panel.contentView().frame()
        pad_x, pad_y = 14, 6
        max_w = frame.size.width - 2 * pad_x
        size = self.label.cell().cellSizeForBounds_(NSMakeRect(0, 0, max_w, 10000))
        lw, lh = min(size.width, max_w), size.height
        bw, bh = lw + 2 * pad_x, lh + 2 * pad_y
        # Bubble sits at the bottom-centre of the panel and hugs the text.
        self.bubble.setFrame_(NSMakeRect((frame.size.width - bw) / 2, 0, bw, bh))
        self.label.setFrame_(NSMakeRect(pad_x, pad_y, lw, lh))
        self.bubble.setHidden_(False)
        if not self.locked:
            # Make the drag area obvious while positioning.
            self.panel.contentView().layer().setBackgroundColor_(NSColor.colorWithWhite_alpha_(1, 0.12).CGColor())
        else:
            self.panel.contentView().layer().setBackgroundColor_(NSColor.clearColor().CGColor())


class DesktopSubtitles:
    """Ties capture → translator → floating window together."""

    def __init__(self, on_status):
        self.on_status = on_status
        self.window = None
        self.capture = None
        self.client = None
        self.pending = queue.Queue()
        self.running = False

    def start(self, bundle_id):
        if self.window is None:
            self.window = SubtitleWindow()
        self.window.show()
        self.client = TranslatorClient(self._caption_from_thread, self._client_status)
        self.client.start()
        self.capture = AudioCapture(bundle_id, self.client.push, self._error_from_thread)
        self.capture.start()
        self.running = True

    def stop(self):
        self.running = False
        if self.capture:
            self.capture.stop()
        if self.client:
            self.client.stop()
        self.capture = self.client = None
        if self.window:
            self.window.final_text = self.window.partial_text = ""
            self.window.render()
            self.window.hide()

    def set_locked(self, locked):
        if self.window is None:
            self.window = SubtitleWindow()
        self.window.show()
        self.window.set_locked(locked)
        if locked and not self.running:
            self.window.hide()

    # Thread-safe hand-off to the main thread (drained by tick()).
    def _caption_from_thread(self, kind, text, speaker):
        self.pending.put(("caption", kind, text, speaker))

    def _client_status(self, s):
        self.pending.put(("status", s))

    def _error_from_thread(self, msg):
        self.pending.put(("error", msg))

    def tick(self):
        """Call from the main thread a few times per second."""
        while True:
            try:
                item = self.pending.get_nowait()
            except queue.Empty:
                break
            if item[0] == "caption" and self.window:
                self.window.caption(*item[1:])
            elif item[0] == "status":
                self.on_status("Desktop subtitles on" if item[1] == "connected"
                               else "Waiting for the translator…")
            elif item[0] == "error":
                self.on_status(item[1])
                self.stop()
        if self.window:
            self.window.tick()
