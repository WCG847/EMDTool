bl_info = {
    "name": "EMD Tool",
    "author": "WCG847",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "File > Import",
    "description": "Import Entry Model Data (EMD) PlayStation 1 format",
    "category": "Import-Export",
}

import bpy
import struct
from collections import OrderedDict

def rshiftl(val, shift):
    return val >> shift

class HexFile:
    def __init__(self, file_obj):
        self.file_obj = file_obj

    def read_int(self, offset, size):
        self.file_obj.seek(offset)
        data = self.file_obj.read(size)
        if len(data) < size:
            # We reached EOF or partial read
            raise ValueError("Not enough bytes to read an integer.")
        return int.from_bytes(data, byteorder='little')

    def read_string(self, offset, size):
        self.file_obj.seek(offset)
        data = self.file_obj.read(size)
        if len(data) < size:
            # We reached EOF or partial read
            raise ValueError("Not enough bytes to read a string.")
        return data

class EMD(object):
    def __init__(self, filename):
        self.filename = filename
        self.file_obj = open(filename, 'rb+')
        self.file = HexFile(self.file_obj)

        # Read the halfword addend at 0x08
        halfword_addend = self.file.read_int(0x08, 2)

        # Read the base “file size” (or partial) at 0x0A
        size_base = self.file.read_int(0x0A, 2)

        # Calculate final file size or offset
        # If your format specifically says: “(value at 0x0A << 2) + (value at 0x08)”
        self.calculated_file_size = (size_base << 2) + halfword_addend

        print(f"Calculated file size/offset = {self.calculated_file_size}")
        self.file_size = self.calculated_file_size

        # Read relevant header values
        value1 = self.file.read_int(8, 2)
        value2 = self.file.read_int(12, 2)
        value3 = self.file.read_int(14, 2)
        self.offset_A = value1 + value2 * 4 + value3 * 8

        # Prepare to store data
        self.n_vertices_of_mesh = []
        self.obj_info = []
        self.scale = 3276.8
        self.parse_emd()

    def parse_emd(self):
        temp_offset = self.offset_A

        # Try reading the signature at the offset
        # Make sure we haven't gone past file_size
        if temp_offset + 4 > self.file_size:
            print("Error: offset_A goes beyond file size.")
            return

        signature_str = self.file.read_string(temp_offset, 4)
        signature_int = struct.unpack('I', signature_str)[0]
        signature = signature_str

        n_object = 0
        self.scale = float(self.scale)

        # We'll add a chunk counter to avoid infinite loops
        max_outer_loops = 9999  # some high limit
        outer_count = 0

        # Outer loop
        while signature == signature_str:
            outer_count += 1
            if outer_count > max_outer_loops:
                print("Safety break: exceeded max outer loops.")
                break

            temp_dict = OrderedDict()
            vertices = []
            addr_vertices = []
            faces = []

            # Move past the signature we just read
            temp_offset += 4

            # Check offset safety again
            if temp_offset + 4 > self.file_size:
                print("Reached or exceeded file size when reading next temp.")
                break

            temp = self.file.read_int(temp_offset, 4)
            chunk_id = int(temp // 16777216)  # or (temp >> 24)

            print(f"---\nParsing new chunk block at offset 0x{temp_offset:X}")
            print(f"Signature int: 0x{signature_int:X}, temp: 0x{temp:X}, chunk_id={chunk_id}")

            # We'll add a secondary chunk loop limit 
            max_inner_loops = 9999
            inner_count = 0

            # Inner loop
            while temp != signature_int:
                inner_count += 1
                if inner_count > max_inner_loops:
                    print("Safety break: exceeded max inner loops.")
                    break

                # If we've gone past the file size, bail out
                if temp_offset >= self.file_size:
                    print("Offset exceeded file size, stopping parse.")
                    signature = b''  # Force outer loop to end
                    break

                if chunk_id == 0:
                    # Parse vertices
                    self._parse_vertices(temp_offset, vertices, addr_vertices, temp)

                    # The _parse_vertices function updates temp_offset internally
                    # so read the updated offset
                    temp_offset = self.last_offset

                elif chunk_id == 52:
                    # Parse triangular faces
                    self._parse_faces(temp_offset, faces, temp, triangle=True)
                    temp_offset = self.last_offset

                elif chunk_id == 60:
                    # Parse quad faces
                    self._parse_faces(temp_offset, faces, temp, triangle=False)
                    temp_offset = self.last_offset

                else:
                    # If we don't know how to parse this chunk, raise or skip
                    raise NotImplementedError(
                        "Unsupported flag 0x%.8X at offset 0x%.4X" % (temp, temp_offset)
                    )

                # Attempt to read the next chunk
                if temp_offset + 4 <= self.file_size:
                    temp = self.file.read_int(temp_offset, 4)
                    chunk_id = int(temp // 16777216)
                    print(f"Decoding chunk {temp:08X} at offset {temp_offset:04X}, chunk_id={chunk_id}")
                else:
                    print("No more data to read or offset out of range.")
                    signature = b''  # end outer loop
                    break

            # store results from this chunk block
            temp_dict['vertex'] = vertices
            self.n_vertices_of_mesh.append(len(vertices))
            temp_dict['vertex addr'] = addr_vertices
            temp_dict['face'] = faces
            self.obj_info.append(temp_dict)

            # Finally try reading new signature
            # (Check offset again)
            if temp_offset + 4 <= self.file_size:
                signature_str = self.file.read_string(temp_offset, 4)
                signature_int = struct.unpack('I', signature_str)[0]
            else:
                print("Offset out of range for reading next signature.")
                signature_str = b''

        self.n_mesh = len(self.obj_info)
        self.file_obj.close()
        print("Finished parsing EMD.")

    def _parse_vertices(self, offset, vertices, addr_vertices, temp):
        # chunk_id == 0
        n_verts = temp & 255
        vert_offset = offset + 4

        loop_guard = 0
        while n_verts > 3:
            loop_guard += 1
            if loop_guard > 9999:
                print("Safety break in vertex parse.")
                break

            x = struct.unpack('h', self.file.read_string(vert_offset, 2))[0] / self.scale
            y = struct.unpack('h', self.file.read_string(vert_offset + 2, 2))[0] / self.scale
            z = struct.unpack('h', self.file.read_string(vert_offset + 4, 2))[0] / self.scale
            addr_vertices.append(vert_offset)
            vertices.append([x, y, z])

            x = struct.unpack('h', self.file.read_string(vert_offset + 12, 2))[0] / self.scale
            y = struct.unpack('h', self.file.read_string(vert_offset + 14, 2))[0] / self.scale
            z = struct.unpack('h', self.file.read_string(vert_offset + 16, 2))[0] / self.scale
            addr_vertices.append(vert_offset + 12)
            vertices.append([x, y, z])

            x = struct.unpack('h', self.file.read_string(vert_offset + 24, 2))[0] / self.scale
            y = struct.unpack('h', self.file.read_string(vert_offset + 26, 2))[0] / self.scale
            z = struct.unpack('h', self.file.read_string(vert_offset + 28, 2))[0] / self.scale
            addr_vertices.append(vert_offset + 24)
            vertices.append([x, y, z])

            vert_offset += 36
            n_verts -= 3

        # handle leftover vertices if n_verts <= 3
        while n_verts > 0:
            loop_guard += 1
            if loop_guard > 9999:
                print("Safety break in leftover vertex parse.")
                break

            x = struct.unpack('h', self.file.read_string(vert_offset, 2))[0] / self.scale
            y = struct.unpack('h', self.file.read_string(vert_offset + 2, 2))[0] / self.scale
            z = struct.unpack('h', self.file.read_string(vert_offset + 4, 2))[0] / self.scale
            addr_vertices.append(vert_offset)
            vertices.append([x, y, z])
            vert_offset += 12
            n_verts -= 1

        # update last_offset
        self.last_offset = vert_offset

    def _parse_faces(self, offset, faces, temp, triangle=True):
        n_face = temp & 255
        face_offset = offset + 4
        direct = -1

        loop_guard = 0
        while n_face > 0:
            loop_guard += 1
            if loop_guard > 9999:
                print("Safety break in face parse.")
                break

            r10 = self.file.read_int(face_offset, 4)

            if triangle:
                # Triangular faces: chunk_id == 52
                # decode r10
                r12 = (r10 << 3) & 2040
                r13 = rshiftl(r10, 5) & 2040
                r14 = rshiftl(r10, 13) & 2040
                f1 = r12 >> 3
                f2 = r13 >> 3
                f3 = r14 >> 3
                if direct > 0:
                    faces.append([f1, f2, f3])
                else:
                    faces.append([f1, f3, f2])
            else:
                # Quad faces: chunk_id == 60
                r12 = (r10 << 3) & 1016
                r13 = rshiftl(r10, 4) & 1016
                r14 = rshiftl(r10, 11) & 1016
                r15 = rshiftl(r10, 18) & 1016
                f1 = r12 >> 3
                f2 = r13 >> 3
                f3 = r14 >> 3
                f4 = r15 >> 3
                # Add the derived triangles
                faces.append([f1, f2, f3])
                faces.append([f1, f2, f4])
                faces.append([f1, f3, f4])
                faces.append([f2, f3, f4])

            direct *= -1
            face_offset += 12
            n_face -= 1

        self.last_offset = face_offset


# Blender operator for importing EMD
class ImportEMDOperator(bpy.types.Operator):
    bl_idname = "import_scene.emd"
    bl_label = "Import EMD"
    bl_options = {'REGISTER', 'UNDO'}

    # File path for the EMD file
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")

    def execute(self, context):
        # Check if a valid filepath is provided
        if not self.filepath:
            self.report({'ERROR'}, "No file selected.")
            return {'CANCELLED'}

        try:
            emd = EMD(self.filepath)

            # Import the parsed data into Blender
            for obj in emd.obj_info:
                if obj['vertex'] and obj['face']:
                    mesh = bpy.data.meshes.new("EMD_Object")
                    mesh.from_pydata(obj['vertex'], [], obj['face'])
                    mesh.update()

                    obj = bpy.data.objects.new("EMD_Object", mesh)
                    context.collection.objects.link(obj)

        except Exception as e:
            self.report({'ERROR'}, f"Failed to import EMD: {str(e)}")
            return {'CANCELLED'}

        return {'FINISHED'}

    def invoke(self, context, event):
        # Invoke file browser
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

# Registering the operator and menu
def menu_func_import(self, context):
    self.layout.operator(ImportEMDOperator.bl_idname, text="EMD (PlayStation 1)")

def register():
    bpy.utils.register_class(ImportEMDOperator)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)

def unregister():
    bpy.utils.unregister_class(ImportEMDOperator)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)

if __name__ == "__main__":
    register()
