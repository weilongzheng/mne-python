"""Mayavi/traits GUI for averaging two sets of KIT marker points"""

# Authors: Christian Brodbeck <christianbrodbeck@nyu.edu>
#
# License: BSD (3-clause)

import cPickle as pickle
import os

from mayavi.core.ui.mayavi_scene import MayaviScene
from mayavi.tools.mlab_scene_model import MlabSceneModel
import numpy as np
from pyface.api import confirm, error, FileDialog, OK, YES
from traits.api import HasTraits, HasPrivateTraits, on_trait_change, cached_property, Instance, Property, \
                       Array, Bool, Button, Color, Dict, Enum, File, Float, Int, List, \
                       Range, Str, Tuple
from traitsui.api import View, Item, HGroup, VGroup, CheckListEditor
from traitsui.menu import NoButtons
from tvtk.pyface.scene_editor import SceneEditor

from .coreg import decimate_headshape
from .viewer import HeadViewController, PointObject
from ..fiff.kit.coreg import read_hsp, write_hsp



out_wildcard = ("Pickled head shape (*.pickled)|*.pickled|"
                "Text file (*.txt)|*.txt")
out_ext = ['.pickled', '.txt']



class HeadShape(HasPrivateTraits):
    file = File(exists=True)

    # settings
    resolution = Range(value=35, low=5, high=50, label="Resolution [mm]")
    exclude = List(Int)

    hsp_points = Array(float, shape=(None, 3))
    points = Array(float, shape=(None, 3))
    ref_points = Array(float, shape=(None, 3))
    n_points = Property(depends_on='points')
    n_points_all = Int(0)

    can_save = Property(depends_on='points')
    save_as = Button(label="Save As...")

    view = View(VGroup('file', 'resolution',
                       Item('exclude', editor=CheckListEditor(), style='text'),
                       '_',
                       Item('n_points', label='N Points', style='readonly'),
                       Item('save_as', enabled_when='can_save', show_label=False),
                       label="Head Shape Source", show_border=True))

    @cached_property
    def _get_n_points(self):
        return len(self.points)

    @cached_property
    def _get_can_save(self):
        return np.any(self.points)

    @on_trait_change('file')
    def load(self, fname):
        pts = read_hsp(fname)
        self._cache = {}
        self._exclude = {}
        self.hsp_points = pts

    def _exclude_changed(self, old, new):
        """Validate the values of the exclude list"""
        if not hasattr(self, '_cache'):
            return

        items = set(self.exclude)

        for i in sorted(items):
            if i > self.n_points_all or i < 0:
                items.remove(i)

        exclude = sorted(items)
        self._exclude[self.resolution] = exclude
        self.exclude = exclude
        self.update_points()

    def _exclude_items_changed(self, old, new):
        """This hack is necessary to update the editor for exclude"""
        items = list(self.exclude)
        if items:
            items.append(items[0])
        else:
            items.append(-1)
        self.exclude = items

    @on_trait_change('hsp_points')
    def update_ref_points(self):
        self.ref_points = self.get_dec_points(10)

    @on_trait_change('hsp_points,resolution')
    def update_base_points(self):
        if not hasattr(self, '_cache'):
            return

        res = self.resolution
        self.exclude = self._exclude.get(res, [])
        self.update_points()

    def update_points(self):
        res = self.resolution
        pts = self.get_dec_points(res)
        self.n_points_all = len(pts)
        if self.exclude:
            sel = np.ones(len(pts), dtype=bool)
            sel[self.exclude] = False
            pts = pts[sel]

        self.points = pts

    def get_dec_points(self, res):
        if not hasattr(self, '_cache'):
            raise RuntimeError("No hsp file loaded")

        if res in self._cache:
            pts = self._cache[res]
        else:
            pts = decimate_headshape(self.hsp_points, res)
            self._cache[res] = pts

        return pts

    def cache(self, resolutions=xrange(30, 40)):
        for res in resolutions:
            self.get_dec_points(res)

    def _save_as_fired(self):
        dlg = FileDialog(action="save as", wildcard=out_wildcard,
                         default_path=self.file)
        dlg.open()
        if dlg.return_code != OK:
            return

        ext = out_ext[dlg.wildcard_index]
        path = dlg.path
        if not path.endswith(ext):
            path = path + ext
            if os.path.exists(path):
                msg = ("The file %r already exists. Should it be replaced"
                       "?" % path)
                answer = confirm(None, msg, "Overwrite File?")
                if answer != YES:
                    return

        if ext == '.pickled':
            pts = np.asarray(self.points)
            pts_hd = np.asarray(self.ref_points)
            food = {'hsp': pts, 'hsp_hd': pts_hd}
            with open(path, 'w') as fid:
                pickle.dump(food, fid)
        elif ext == '.txt':
            write_hsp(path, self.points)
        else:
            error(None, "Not Implemented: %r" % ext)



class ControlPanel(HasTraits):
    scene = Instance(MlabSceneModel, ())
    headview = Instance(HeadViewController)
    headshape = Instance(HeadShape)
    headobj = Instance(PointObject)
    headobj_ref = Instance(PointObject)

    view = View(VGroup(Item('headshape', style='custom'),
                       VGroup(Item('headobj', show_label=False, style='custom'),
                              label='Decimated Head Shape',
                              show_border=True),
                       VGroup(Item('headobj_ref', show_label=False,
                                   style='custom'),
                              label='Reference Head Shape',
                              show_border=True),
                       Item('headview', style='custom'),
                       show_labels=False,
                       ))

    @on_trait_change('scene.activated')
    def _init_plot(self):
        self.headshape.sync_trait('points', self.headobj, 'points')
        self.headshape.sync_trait('ref_points', self.headobj_ref, 'points')

        fig = self.scene.mayavi_scene
        self.picker = fig.on_mouse_pick(self.picker_callback)
        self.picker.tolerance = 0.001

    @on_trait_change('headshape.file')
    def _on_file_changes(self):
        if self.headview:
            self.headview.left = True

    def picker_callback(self, picker):
        mygl = self.headobj.glyph
        if picker.actor not in mygl.actor.actors:
            return

        n = len(mygl.glyph.glyph_source.glyph_source.output.points)
        point_id = picker.point_id / n

        # If the no points have been selected, we have '-1'
        if point_id == -1:
            return

        idx = point_id
        for e_idx in sorted(self.headshape.exclude):
            if idx >= e_idx:
                idx += 1

        self.headshape.exclude.append(idx)



class MainWindow(HasTraits):
    """GUI for interpolating between two KIT marker files"""
    scene = Instance(MlabSceneModel, ())

    headshape = Instance(HeadShape)
    headobj = Instance(PointObject)
    headobj_ref = Instance(PointObject)
    headview = Instance(HeadViewController)

    panel = Instance(ControlPanel)

    def _headshape_default(self):
        hs = HeadShape()
        return hs

    def _headobj_default(self):
        color = tuple(int(c * 255) for c in (.1, .9, 1))
        ho = PointObject(scene=self.scene, points=self.headshape.points,
                         color=color, point_scale=5)
        return ho

    def _headobj_ref_default(self):
        color = tuple(int(c * 255) for c in (.9, .9, .9))
        ho = PointObject(scene=self.scene, points=self.headshape.ref_points,
                         color=color, point_scale=2)
        return ho

    def _headview_default(self):
        hv = HeadViewController(scene=self.scene, scale=160, system='ARI')
        return hv

    def _panel_default(self):
        return ControlPanel(scene=self.scene, headview=self.headview,
                            headshape=self.headshape, headobj=self.headobj,
                            headobj_ref=self.headobj_ref)

    view = View(HGroup(Item('scene',
                            editor=SceneEditor(scene_class=MayaviScene)),
                       Item('panel', style="custom"),
                       show_labels=False,
                      ),
                resizable=True,
                height=0.75, width=0.75,
                buttons=NoButtons)
