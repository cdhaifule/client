"""Copyright (C) 2013 COLDWELL AG

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.
"""

import os
import gevent
from Tkinter import Tk, Frame, Label, StringVar, RAISED
#from .animate import AnimatedImgLabel
from .... import settings
from PIL import Image, ImageTk

class Splash(Tk):
    def __init__(self):
        Tk.__init__(self)

        # frame
        frame = Frame(self, padx=10, pady=10, bd=1, relief=RAISED)
        frame.pack()

        # logo
        self.logo = Image.open(os.path.join(settings.img_dir, 'logo_big.png'))
        self.logo = self.logo.convert('RGBA')
        r, g, b, self.logo_alpha = self.logo.split()

        # loading image
        self.angle = 0
        self.oimg = Image.open(os.path.join(settings.img_dir, 'circle_big.png'))
        self.oimg = self.oimg.convert('RGBA')
        img = ImageTk.PhotoImage(self.oimg, master=self)
        self.w = Label(frame, image=img, padx=5, pady=5)
        self.w.image = img
        self.w.pack()

        # center window to screen
        x = (self.winfo_screenwidth() - img.width()) // 2
        y = (self.winfo_screenheight() - img.height()) // 2
        self.geometry('+{}+{}'.format(x, y))

        self.overrideredirect(True)
        self.wm_attributes("-topmost", 1)
        self.focus_force()

        self.update()
        self.greenlet = gevent.spawn_later(0.08, self.animate)

    def animate(self):
        img = self.oimg.rotate(self.angle)
        img.paste(self.logo, mask=self.logo_alpha)
        img = ImageTk.PhotoImage(img, master=self)
        self.w.config(image=img)
        self.update()
        self.angle += 4
        if self.angle > 360:
            self.angle = 0
        self.greenlet = gevent.spawn_later(0.0040, self.animate)

    def set_text(self, text):
        pass
        #self.text.set(text)
        #self.update()

    def show(self):
        self.deiconify()
        self.update()

    def hide(self):
        self.withdraw()

    def close(self):
        self.greenlet.kill()
        self.destroy()
