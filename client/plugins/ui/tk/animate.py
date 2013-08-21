import io
import base64
import gevent

from Tkinter import Label
from PIL import ImageTk, Image

class AnimatedImgLabel(Label):
    # http://stackoverflow.com/questions/7960600/python-tkinter-display-animated-gif-using-pil
    def __init__(self, master, data, encoding='base64', **kwargs):
        if encoding == 'base64':
            data = base64.b64decode(data)
        self.img = Image.open(io.BytesIO(data))

        seq = list()
        try:
            while True:
                seq.append(self.img.copy())
                self.img.seek(len(seq)) # skip to next frame
        except EOFError:
            pass # we're done

        try:
            self.delay = float(self.img.info['duration'])/1000
        except KeyError:
            self.delay = 0.200

        self.frames = list()

        for frame in seq:
            #frame = frame.convert('RGBA')
            self.frames.append(ImageTk.PhotoImage(frame))

        self.idx = 0
        self.first = self.frames[0]
        Label.__init__(self, master, image=self.first, **kwargs)

        self.greenlet = gevent.spawn_later(self.delay, self.play)

    def destroy(self):
        self.greenlet.kill()
        Label.destroy(self)

    def play(self):
        try:
            self.config(image=self.frames[self.idx])
            self.master.update()
            self.idx += 1
            if self.idx == len(self.frames):
                self.idx = 0
            self.greenlet = gevent.spawn_later(self.delay, self.play)
        except:
            import traceback
            traceback.print_exc()
            raise
