"""Output-side EOF observation; contains no schedule or notice policies."""


def observe_audio_source(base_class, source):
    class ObservedSource(base_class):
        def __init__(self):
            self.reached_eof = False
            self.frames_read = 0
            self.cleaned = False

        def read(self):
            frame = source.read()
            if frame:
                self.frames_read += 1
            else:
                self.reached_eof = True
            return frame

        def is_opus(self):
            return source.is_opus()

        def cleanup(self):
            if not self.cleaned:
                self.cleaned = True
                source.cleanup()

        def __getattr__(self, name):
            return getattr(source, name)

    return ObservedSource()
