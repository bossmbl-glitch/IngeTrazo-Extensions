# SPDX-License-Identifier: GPL-3.0-or-later
"""Observe Python stdout from the official operator; summarize known verbosity."""
import re


class OfficialLogCapture:
    def __init__(self,stream,detailed=False,limit=3000):
        self.stream=stream;self.detailed=detailed;self.limit=limit;self.pending='';self.dropping=False
        self.materials=[];self.mesh_material_ids=set();self.material_count=0;self.mesh_count=0
        self.triangles=0;self.empty_geometry=0;self.suppressed_lines=0;self.errors=[]

    def __getattr__(self,name):return getattr(self.stream,name)

    @staticmethod
    def verbose(line):
        return (line.startswith('[D5] send_mat: ') or line.startswith('[D5] send_mesh[')
                or line.startswith('[D5] do_sync final: ')
                or (line.startswith('[D5-DIAG] ') and '_prepare_mesh=None (no geometry)' in line))

    def write(self,value):
        if self.detailed:self.stream.write(value)
        self.pending+=value
        while '\n' in self.pending:
            line,self.pending=self.pending.split('\n',1)
            if not self.dropping:self._line(line+'\n')
            self.dropping=False
        if len(self.pending)>65536:
            # Bound multiline buffering, including enormous object-name lists.
            if not self.dropping:self._line(self.pending[:65536]+' [capture truncated]\n')
            self.pending='';self.dropping=True
        return len(value)

    def _line(self,line):
        try:
            material=re.match(r'\[D5\] send_mat: (.*?) id=([^ ]+)',line)
            if material:
                self.material_count+=1
                if len(self.materials)<self.limit:self.materials.append({'name':material[1][:200],'logged_id_prefix':material[2][:64]})
            mesh=re.match(r'\[D5\] send_mesh\[[^]]+\]: .*?mats=\[(.*?)\].*?tris=(\d+)',line)
            if mesh:
                self.mesh_count+=1;self.triangles+=int(mesh[2])
                self.mesh_material_ids.update(re.findall(r"'([^']+)'",mesh[1]))
            if line.startswith('[D5-DIAG] ') and '_prepare_mesh=None (no geometry)' in line:self.empty_geometry+=1
        except Exception as exc:self.errors=[str(exc)[:300]]
        if not self.detailed:
            if self.verbose(line):self.suppressed_lines+=1
            else:self.stream.write(line)

    def flush(self):self.stream.flush()

    def finish(self):
        if self.pending and not self.dropping:self._line(self.pending)
        self.pending='';self.flush()

    def summary(self):
        unmatched=[m for m in self.materials if m['logged_id_prefix'] not in self.mesh_material_ids]
        return {'materials_logged':self.material_count,'meshes_logged':self.mesh_count,
                'triangles_logged':self.triangles,'empty_geometry_logged':self.empty_geometry,
                'sent_materials_not_referenced_in_logged_meshes':len(unmatched),
                'unreferenced_sent_materials':unmatched,'material_details_truncated':self.material_count>len(self.materials),
                'suppressed_verbose_lines':self.suppressed_lines,'detailed_log':self.detailed,
                'capture_errors':self.errors,
                'basis':'Observed Python text only; IDs are logged prefixes. Native output and SDK dependencies may differ.'}
