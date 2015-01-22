# -*- coding: utf-8 -*-

#
# totem-danmaku.py
#  Danmaku Support for Totem Video Player (based on concepts of CommentCoreLibrary)
#
# Copyright (C) 2015 Jim Chen <knh.jabbany@gmail.com>
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

from gi.repository import GObject, Peas, Gtk, GLib, GtkClutter, Clutter, Pango # pylint: disable-msg=E0611
import bisect
import sys

class DanmakuPlugin (GObject.Object, Peas.Activatable):
    __gtype_name__ = 'DanmakuPlugin'

    object = GObject.property (type = GObject.Object)

    def __init__ (self):
        GObject.Object.__init__ (self)
        self._totem = None

    # totem.Plugin methods
    def do_activate (self):
        self._totem = self.object
        
        # add the danmaku stage
        video = self._totem.get_video_widget()
        self._cm = CommentManager(self._totem)
        video.get_stage().add_child(self._cm)
        video.get_toplevel().connect("window-state-event", self._cm.state_change)
        video.get_toplevel().connect("configure-event", self._cm.set_bounds)
        video.connect("size-allocate", self._cm.set_bounds)
        # add signal to update the danmaku stage
        self._tick_signal_handler = video.connect("tick", self.tick_handler)
        self._play_signal_handler = self._totem.connect("file-has-played", self.play_handler)
        self._end_signal_handler = video.connect("eos", self.end_handler)
        # debugging
        cmt = CoreComment(1, '你好', 0)
        self._cm.load([cmt])

    def do_deactivate (self):
        # Include the plugin destroying Actions
        self._totem = None
    
    def seek_handler (self, video, forward, user_data):
        self._cm.set_bounds()
    
    def tick_handler (self, video, cur_time, st_length, cur_pos, user_data):
        if self._totem.is_playing():
            if not self._cm.isPlaying:
                self._cm.resume()
        else:
            self._cm.stop()
        self._cm.time(cur_time)
        
    def play_handler (self, murl, user_data):
        self._cm.resume()
        
    def end_handler (self, video, user_data):
        self._cm.stop()

class CoreComment ():

    DEFAULT_LIFETIME = 8000
    DEFAULT_FONTNAME = "SimHei"
    
    def __init__(self, mode, text, stime, color = "#ffffff", size = 25):
        # load the danmaku defaults    
        self.text = text
        self.color = color
        self.mode = mode
        self.size = size
        self.stime = stime
        self.font = self.DEFAULT_FONTNAME
        self.dur = self.DEFAULT_LIFETIME
        self.ttl = self.DEFAULT_LIFETIME
        # init default positions
        self._y = 0
        self._width = None
        self._drawObject = None
        
    def set_duration (self, duration, reset = False):
        # sets the duration
        self.dur = duration
        if reset:
            # also reset the ttl
            self.ttl = duration
    def get_font_string (self):
        return " ".join([self.font, str(self.size) + "px"])
    
class CommentManager (Clutter.Actor):
    def __init__(self, totem):
        super(Clutter.Actor, self).__init__()
        self._totem = totem
        self.runline = []
        self.timeline = []
        self._timeline_keys = []
        self.position = 0
        self.playtime = 0
        
        self.width = None
        self.height = None
        
        self.isPlaying = False
        
        # bind timers
        GLib.timeout_add(41, self.timer)
        
    def load(self, timeline):
        # Maintains a sorted timeline
        self.timeline = sorted(timeline, cmp=lambda x,y: cmp(x.stime, y.stime))
        self._timeline_keys = [comment.stime for comment in self.timeline]
        self.position = 0
        
    def seek(self, time):
        # Seek to position
        self.position = bisect.bisect_left(self._timeline_keys, time)
        
    def resume(self):
        self.isPlaying = True
        
    def stop(self):
        self.isPlaying = False
        
    def time(self, time):
    	# Check if UI is ready
    	if self.width == None or self.height == None:
    	    return;
        # Update time
        old_pos = self.position
        self.seek(time)
        if self.position > old_pos:
            for i in range(old_pos, self.position):
                self.send(self.timeline[i])
                
        # Remove comments which are dead
        for cmt in self.runline:
            if cmt.ttl <= 0:
                self.remove_child(cmt._drawObject)
        self.runline = [cmt for cmt in self.runline if cmt.ttl > 0]
        
        # Draw the comments and age them
        self.playtime = time
    
    def send(self, comment):
        if not isinstance(comment, CoreComment):
            raise Exception('Must pass a CoreComment in order to send')
        
        text = Clutter.Text()
        text.set_color(Clutter.Color.from_string(comment.color)[1]);
        text.set_text(comment.text)
        text.set_font_name(comment.get_font_string())
        comment._drawObject = text
        if comment._width == None:
            comment._width = comment._drawObject.get_width()
        x = float(self.width + comment._width)
        comment._drawObject.set_position(x, comment._y)
        self.add_child(text)
        
        self.runline.append(comment)
        
    def timer(self, *args):
        if not self.isPlaying:
            return True
        for cmt in self.runline:
            if cmt.mode == 1:
                if cmt._width == None:
                    cmt._width = cmt._drawObject.get_width()
                x = (cmt.ttl / float(cmt.dur)) * float(self.width + cmt._width) - cmt._width
                cmt._drawObject.set_position(x, cmt._y)
                cmt.ttl -= 41
        return True
    
    def state_change(self, *arg):
        self.set_bounds()
        return False
        
    def set_bounds(self, *arg):
        stage = self.get_stage()
        s_height = stage.get_height()
        s_width = stage.get_width() 
        self.width = s_width
        self.height = s_height
        
        # Set actor dimensions
        self.set_position(0,0);
        self.set_size(s_width, s_height)
        
        return False
