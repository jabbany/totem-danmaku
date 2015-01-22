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
import time
import struct
import re
import xml.dom.minidom as domParser
import urllib2, gzip, zlib
from StringIO import StringIO

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
        comments = remoteDanmaku("http://comment.bilibili.com/2436827.xml")
        self._cm.load(comments)

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
        self._x = 0
        self._width = None
        self._height = None
        self._drawObject = None
        
    def set_duration (self, duration, reset = False):
        # sets the duration
        self.dur = duration
        if reset:
            # also reset the ttl
            self.ttl = duration
    def get_font_string (self):
        return " ".join([self.font, str(self.size) + "px"])

class SpaceAllocator ():
    width = 0
    height = 0
    pools = [[]]
    
    def add(self, comment):
        if comment._height > self.height:
            comment._cid = -1;
            comment._y = 0;
        else:
            comment._y = self.allocate(comment, 0);
            # we should keep the pools sorted but what the heck this is a simpler impl
            self.pools[comment._cid].append(comment)
            
    def allocate(self, comment, cindex = 0):
        # try to see if the comment can fit in the given pool
        while len(self.pools) <= cindex:
            self.pools.append([])
        
        pool = self.pools[cindex]
        if len(pool) == 0:
            comment._cid = cindex;
            return 0;
        if self.path_check(comment, 0, pool):
            comment._cid = cindex;
            return 0;
        ypool = [(cmt._y + cmt._height + 1) for cmt in pool]
        ypool.sort()
        for y in ypool:
            if y + comment._height > self.height:
                continue;
            else:
                if self.path_check(comment, y, pool):
                    comment._cid = cindex;
                    
                    return y
                else:
                    continue
        return self.allocate(comment, cindex + 1)
            
    def will_collide(self, A, B):
        # naive way to do collision detection
        return A.stime + A.ttl >= B.stime + B.ttl / 2;
        
    def path_check(self, target, y, pool = None):
        if pool == None:
            pool = pools[0]
        
        for comment in pool:
            if comment._y > y + target._height or comment._y + comment._height < y:
                continue; # not related comment
            elif comment._x + comment._width < target._x or comment._x > target._x + target._width:
                if self.will_collide(comment, target):
                    return False
                else:
                    continue;
            else:
                return False
        return True # no conflicts found
            
    def free(self, comment):
        if comment._cid >= 0:
            if len(self.pools) > comment._cid:
                if comment in self.pools[comment._cid]:
                    self.pools[comment._cid].remove(comment)
                else:
                    raise Exception([comment._cid, comment.text, self.pools[comment._cid], comment])
    
    def set_bounds(self, width, height):
        self.width = width
        self.height = height

class CommentManager (Clutter.Actor):
    def __init__(self, totem):
        super(Clutter.Actor, self).__init__()
        self._totem = totem
        self.runline = []
        self.timeline = []
        self._timeline_keys = []
        self.position = 0
        self.playtime = 0
        
        self.allocator = SpaceAllocator()
        
        self.width = None
        self.height = None
        
        self.isPlaying = False
        
        # bind timers
        GLib.timeout_add(20, self.timer)
        self._timerTime = time.time() * 1000
        
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
        prepare = []
        for cmt in self.runline:
            if cmt.ttl <= 0:
                self.allocator.free(cmt)
                prepare.append(cmt)
                self.remove_child(cmt._drawObject)
                self.remove_child(cmt._shadowBR)
                self.remove_child(cmt._shadowBL)
                self.remove_child(cmt._shadowTR)
                self.remove_child(cmt._shadowTL)
        for cmt in prepare:
            self.runline.remove(cmt)
        
        # Draw the comments and age them
        self.playtime = time
    
    def send(self, comment):
        if not isinstance(comment, CoreComment):
            raise Exception('Must pass a CoreComment in order to send')
        if comment.mode != 1:
            return; # Only support scrolling for now
        text = Clutter.Text()
        text.set_color(Clutter.Color.from_string(comment.color)[1]);
        text.set_text(comment.text)
        text.set_font_name(comment.get_font_string())
        
        # Add a few extra text objects
        shadowBR = Clutter.Text()
        shadowBR.set_color(Clutter.Color.from_string("#000000")[1]);
        shadowBR.set_text(comment.text)
        shadowBR.set_font_name(comment.get_font_string())
        
        shadowTL = Clutter.Text()
        shadowTL.set_color(Clutter.Color.from_string("#000000")[1]);
        shadowTL.set_text(comment.text)
        shadowTL.set_font_name(comment.get_font_string())
        
        shadowBL = Clutter.Text()
        shadowBL.set_color(Clutter.Color.from_string("#000000")[1]);
        shadowBL.set_text(comment.text)
        shadowBL.set_font_name(comment.get_font_string())
        
        shadowTR = Clutter.Text()
        shadowTR.set_color(Clutter.Color.from_string("#000000")[1]);
        shadowTR.set_text(comment.text)
        shadowTR.set_font_name(comment.get_font_string())
        
        comment._drawObject = text
        comment._shadowBR = shadowBR
        comment._shadowTL = shadowTL
        comment._shadowBL = shadowBL
        comment._shadowTR = shadowTR
        
        if comment._width == None or comment._height == None:
            comment._width = comment._drawObject.get_width() + 1
            comment._height = comment._drawObject.get_height() + 1
        comment._x = float(self.width + comment._width)
        x = comment._x
        # set position
        comment._drawObject.set_position(x, comment._y)
        comment._shadowBR.set_position(x + 1, comment._y + 1)
        comment._shadowTL.set_position(x - 1, comment._y - 1)
        comment._shadowBL.set_position(x - 1, comment._y + 1)
        comment._shadowTR.set_position(x + 1, comment._y - 1)
        # add text and shadow
        self.add_child(shadowBR)
        self.add_child(shadowTL)
        self.add_child(shadowBL)
        self.add_child(shadowTR)
        self.add_child(text)
        
        self.allocator.add(comment)
        
        self.runline.append(comment)
        
    def timer(self, *args):
        if not self.isPlaying:
            self._timerTime = time.time() * 1000
            return True
        for cmt in self.runline:
            if cmt.mode == 1:
                if cmt._width == None or cmt._height == None:
                    cmt._width = cmt._drawObject.get_width() + 1
                    cmt._height = cmt._drawObject.get_height() + 1
                cmt._x = (cmt.ttl / float(cmt.dur)) * float(self.width + cmt._width) - cmt._width
                x = cmt._x
                cmt._drawObject.set_position(x, cmt._y)
                cmt._shadowBR.set_position(x + 1, cmt._y + 1)
                cmt._shadowTL.set_position(x - 1, cmt._y - 1)
                cmt._shadowBL.set_position(x - 1, cmt._y + 1)
                cmt._shadowTR.set_position(x + 1, cmt._y - 1)
                cmt.ttl -= (time.time() * 1000 - self._timerTime)
        self._timerTime = time.time() * 1000
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
        self.allocator.set_bounds(self.width, self.height)
        # Set actor dimensions
        self.set_position(0,0);
        self.set_size(s_width, s_height)
        
        return False
 
# Methods
def parseBilibiliFormat(text):
    # instead of actually taking time to parse, we'll be lazy and use minidom
    try:
        dom = domParser.parseString(text.decode('utf-8'))
        comments = dom.getElementsByTagName('d')
        for comment in comments:
            try:
                params = str(comment.getAttribute('p')).split(',')
                color = int(params[3])
                rgb = int(color / 65536), int((color % 65536) / 256), color % 25656
                yield CoreComment(int(params[1]), str(comment.childNodes[0].wholeText).replace('/n',"\n"), float(params[0]) * 1000, "#" + struct.pack('BBB',*rgb).encode('hex'), int(params[2]))
            except Exception as e:
                continue; # ignore all exceptions
    except Exception:
        for comment in re.finditer(r'd p="(.+)">(.+)</', text):
            params = comment.group(1).split(',')
            ctext = comment.group(2)
            color = int(params[3])
            rgb = int(color / 65536), int((color % 65536) / 256), color % 256
            yield CoreComment(int(params[1]), ctext.replace('/n',"\n"), float(params[0]) * 1000, "#" + struct.pack('BBB',*rgb).encode('hex'), float(params[2]))

def remoteDanmaku(url):
    request = urllib2.Request(url)
    request.add_header('Accept-encoding', 'gzip')
    response = urllib2.urlopen(request)
    if response.info().get('Content-Encoding') == 'gzip':
        buf = StringIO( response.read())
        f = gzip.GzipFile(fileobj=buf)
        data = f.read()
        fl = open('file','w')
        fl.write(data);
        fl.close()
        return [comment for comment in parseBilibiliFormat(data)]
    elif response.info().get('Content-Encoding') == 'deflate':
        return [comment for comment in parseBilibiliFormat(zlib.decompressobj(-zlib.MAX_WBITS).decompress(response.read()))]
    else:
        return [comment for comment in parseBilibiliFormat(response.read())]
        
if __name__ == '__main__':
    for dm in remoteDanmaku("http://comment.bilibili.com/2952655.xml"):
        print dm.text, dm.color
