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
        self.frame = Frame(self, padx=10, pady=10, bd=1, relief=RAISED)
        self.frame.pack()

        # logo
        logo = Image.open(os.path.join(settings.img_dir, 'logo_big.png'))
        logo = logo.convert('RGBA')
        r, g, b, logo_alpha = logo.split()

        # load image
        self.angle = 0
        circle = Image.open(os.path.join(settings.img_dir, 'circle_big.png'))
        circle = circle.convert('RGBA')
        img = ImageTk.PhotoImage(circle, master=self)

        # status text
        self.text = StringVar()
        
        # center window to screen
        x = (self.winfo_screenwidth() - img.width()) // 2
        y = (self.winfo_screenheight() - img.height()) // 2
        self.geometry('+{}+{}'.format(x, y))

        self.overrideredirect(True)
        self.wm_attributes("-topmost", 1)
        self.focus_force()

        self.update()

        self.index = 0
        self.angles = list()
        for i in xrange(0, 360, 5):
            img = circle.rotate(i)
            img.paste(logo, mask=logo_alpha)
            img = ImageTk.PhotoImage(img, master=self)
            self.angles.append(img)

        self.w = Label(self.frame, padx=5, pady=5)
        self.w.image = img
        self.w.pack()
        
        self.greenlet = gevent.spawn_later(0.015, self.animate)
        self.info_greenlet = gevent.spawn_later(50, self.show_info)

    def animate(self):
        self.index += 1
        if len(self.angles) == self.index:
            self.index = 0
        img = self.angles[self.index]
        self.w.config(image=img)
        self.update()
        self.angle += 4
        if self.angle > 360:
            self.angle = 0
        self.greenlet = gevent.spawn_later(0.02, self.animate)

    def show_info(self):
        w = Label(self.frame, textvariable=self.text, padx=5, pady=5)
        w.pack()
        self.update()

    def set_text(self, text):
        self.text.set('\n'.join(text.split(': ', 1)))
        self.update()

    def show(self):
        self.deiconify()
        self.update()

    def hide(self):
        self.withdraw()

    def close(self):
        self.greenlet.kill()
        if self.info_greenlet:
            self.info_greenlet.kill()
        self.destroy()
