from printq.arm.piper import PiperArm
from printq.camera.qwen import Qwen
from printq.camera.realsense import RealsenseCamera
import threading
import redis
import json
import time

arm = PiperArm()
qwen = Qwen()
camera = RealsenseCamera()

r = redis.Redis(host='localhost', port=6379, decode_responses=True)

do_loop = False

def restart_loop():
    pubsub = r.pubsub()
    pubsub.subscribe('start')
    print("Subscribed to Redis channel 'time_control'. Listening for messages...")
    for _ in pubsub.listen():
        if not do_loop:
            do_loop = True

if __name__ == "__main__":
    threading.Thread(target=restart_loop, daemon=True).start()
    arm.start()
    while True:
        camera.show_frame()
        camera.show_obj()
        if do_loop:
            arm.go_to_ready()
            time.sleep(1)
            arm.go_to_pregrasp()
            time.sleep(1)
            arm.go_to_grasp()
            time.sleep(1)
            distance, bbox = camera.get_obj()
            arm.move_ik(0, distance, 0.2, 0)
            pic = camera.take_pic()
            status, message = qwen.get_print_status(pic)
            r.publish('status', json.dumps({
                        "success": status,
                        "desc": message,
                        "image": pic}))
            if status:
                arm.go_to_good_bin()
            else:
                arm.go_to_bad_bin()
            arm.go_to_ready()
            do_loop = False