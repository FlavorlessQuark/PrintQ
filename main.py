import zipfile
import os

import time
import bambulabs_api as bl

IP = ''
SERIAL = ''
ACCESS_CODE = ''

def extract_gcode_from_3mf(three_mf_path, output_dir="extracted_gcode"):
    with zipfile.ZipFile(three_mf_path, 'r') as zip_ref:
        # G-code is typically in the 'Metadata' or '3D' folder depending on the slicer
        # For Bambu/Orca, it's often 'Metadata/plate_1.gcode'
        gcode_files = [f for f in zip_ref.namelist() if f.endswith('.gcode')]
        
        if not gcode_files:
            print("No G-code found. This 3MF might contain only 3D geometry.")
            return

        zip_ref.extractall(output_dir, members=gcode_files)
        return gcode_files

if __name__ == "__main__":
    printer = bl.Printer(IP, ACCESS_CODE, SERIAL)
    printer.connect()

    status = printer.get_state()
    printer.disconnect()

    gcode = extract_gcode_from_3mf("my_project.3mf")
    printer.gcode(gcode)
    while True:
        time.sleep(15)  # Check every 5 seconds
        status = printer.getState()
        if (status == bl.GcodeState.FINISHED):
            printer.move_z_axis(256)
            print("finished printintg, moving z axis up")
        # do the next one
