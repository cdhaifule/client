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

#import os
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

        # image
        img = self.img = ImageTk.PhotoImage(Image.open(settings.mainicon), master=self)
        w = Label(frame, image=self.img, padx=5, pady=5)
        w.image = self.img
        w.pack()
        #with open(os.path.join(settings.img_dir, 'loading_tk.gif'), 'rb') as f:
        #    data = f.read()
        #self.img = AnimatedImgLabel(frame, data, 'raw', padx=5, pady=5)
        #img = self.img.first
        #self.img.pack()

        # status text
        self.text = StringVar()
        w = Label(frame, textvariable=self.text, padx=5, pady=5)
        w.pack()

        # center window to screen
        x = (self.winfo_screenwidth() - img.width()) // 2
        y = (self.winfo_screenheight() - img.height()) // 2
        self.geometry('+{}+{}'.format(x, y))

        self.overrideredirect(True)
        self.focus_force()

        self.update()

    def set_text(self, text):
        self.text.set(text)
        self.update()

    def show(self):
        self.deiconify()
        self.update()

    def hide(self):
        self.withdraw()

    def close(self):
        self.destroy()
