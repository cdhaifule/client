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

from .scheme import Table, Column, intervalled

class Progress(Table, intervalled.Cache):
    _table_name = 'progress'
    
    id = Column("api")
    progress = Column("api")

    def __init__(self, id=None, max=0, current=0):
        self.id = id
        self.current = current
        self.max = max
        self.dirty = True
        self.commit()

    def set(self, max, current):
        self.dirty = self.max != max or self.current != current
        self.max = max
        self.current = current

    def set_max(self, max):
        self.dirty = self.max != max
        self.max = max

    def add_max(self, amount):
        self.set_max(self.max + amount)

    def set_current(self, current):
        self.dirty = self.current != current
        self.current = current

    def add_current(self, amount):
        self.set_current(min(self.max, self.current + amount))
        
    def __add__(self, amount):
        self.add(amount)
        return self
        
    def __iadd__(self, amount):
        self.add(amount)
        return self
        
    def commit(self):
        if self.dirty:
            self.progress = self.max > 0 and float(self.current)/self.max or 0.0
            self.dirty = False
