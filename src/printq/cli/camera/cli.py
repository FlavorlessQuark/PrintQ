"""Camera control commands."""

from logging import getLogger
import threading

import click
import redis
from printq.camera.qwen import Qwen
import threading

logger = getLogger(__name__)
r = redis.Redis(host='localhost', port=6379, decode_responses=True) 
@click.group(name="camera")
@click.pass_context
def camera_commands(ctx):
    """Camera control utilities"""
    ctx.ensure_object(dict)

def start_loop():
    pubsub = r.pubsub()
    pubsub.subscribe('start')
    print("Subscribed to Redis channel 'time_control'. Listening for messages...")
    for message in pubsub.listen():
        print(f"Received message: {message}")

@camera_commands.command(name="start")
def start():
    """Start the camera and stream the depth feed until 'q' is pressed."""
    import cv2
    import base64
    import numpy as np
    import json
    from printq.camera.realsense import RealsenseCamera

    camera = RealsenseCamera()
    qwen = Qwen()
    show = 1
    threading.Thread(target=start_loop, daemon=True).start()
    try:
        while True:
            if show:
                camera.show_frame()
                camera.get_obj()
            # waitKey(1) both pumps the OpenCV GUI event loop (so the window
            # updates) and polls for a quit key. Returns -1 if no key.
            key = cv2.waitKey(1)
            if key:
                match key & 0xFF:
                    case 113:#q
                        break
                    case 115:#s
                        show ^= 1
                    case 100:#d
                        show ^= 1
                        pic = camera.take_pic()
                        status, message = qwen.get_print_status(pic)
                        r.publish('status', json.dumps({
                                    "success": status,
                                    "desc": message,
                                    "image": pic}))

    finally:
        camera.close()

@camera_commands.command(name="serial")
def serial():

    from printq.camera.realsense import RealsenseCamera

    camera = RealsenseCamera()

    camera.get_serial()



@camera_commands.command(name="calibrate")
def calibrate():
    from printq.camera.pointclound import PointCloud
    pointcloud = PointCloud()
    pointcloud.calibrate()

@camera_commands.command(name="cmp_cam")
def calibrate():
    from printq.camera.pointclound import PointCloud
    from printq.camera.realsense import RealsenseCamera
    pointcloud = PointCloud()

    cam = RealsenseCamera()
    pointcloud.load_3mf_as_pointcloud("./src/printq/assets/xyz.3mf")
    print(pointcloud.compare_pointclouds(cam.get_point_cloud() ))


# @camera_commands.command(name="ask")
# def ask_qwen():
#     import os
#     from openai import OpenAI
#     from printq.camera.realsense import RealsenseCamera
#     KEY = "sk-5a54c58071af4e7781b714772fc7a233"
    
#     camera = RealsenseCamera()
#     img_data = camera.take_pic()

#     client = OpenAI(
#         api_key=KEY,
#         base_url=("https://dashscope-intl.alyunc.com/compatible-mode/v1")
#     )
#     completion = client.chat.completions.create(
#         model="qwen3.6-plus",
#         messages=[
#             {"role": "user", 
#              "content": [
#                  {
#                     "type": "text", 
#                     "text": "This is a 3d printed object. Describe the quality of the print in a short sentence blsusb"
#                 }, {
#                     "type": "image_url", 
#                     "image_url": {
#                         "url": f"data:image/jpeg;base64,{img_data}"
#                     }
#                 }
#              ]}])
#     print(completion.choices[0].message.content)
#     import os
#     import dashscope
#     from dashscope import MultiModalConversation
#     # dashscope.base_http_api_url = "https://dashscope-intl.aliyuncs.com/api/v1"

#     messages = [
#         {
#             "role": "user",
#             "content": [
#                 {"image": "https://img.alicdn.com/imgextra/i1/O1CN01gDEY8M1W114Hi3XcN_!!6000000002727-0-tps-1024-406.jpg"},
#                 {"text": "Solve this problem?"}
#             ]
#         }
#     ]

#     response = MultiModalConversation.call(
#         api_key=os.getenv('DASHSCOPE_API_KEY'),
#         model="qwen3-vl-flash",  # Here we use qvq-max as an example; you can change the model name as needed.
#         messages=messages,
#         stream=True,
#     )

#     # Define complete reasoning process
#     reasoning_content = ""
#     # Define complete response
#     answer_content = ""
#     # Check if reasoning has ended and response has started
#     is_answering = False

#     print("=" * 20 + "Reasoning Process" + "=" * 20)

#     for chunk in response:
#         # If both reasoning and response are empty, skip
#         message = chunk.output.choices[0].message
#         reasoning_content_chunk = message.get("reasoning_content", None)
#         if (chunk.output.choices[0].message.content == [] and
#             reasoning_content_chunk == ""):
#             pass
#         else:
#             # If current part is reasoning process
#             if reasoning_content_chunk != None and chunk.output.choices[0].message.content == []:
#                 print(chunk.output.choices[0].message.reasoning_content, end="")
#                 reasoning_content += chunk.output.choices[0].message.reasoning_content
#             # If current part is response
#             elif chunk.output.choices[0].message.content != []:
#                 if not is_answering:
#                     print("\n" + "=" * 20 + "Complete Response" + "=" * 20)
#                     is_answering = True
#                 print(chunk.output.choices[0].message.content[0]["text"], end="")
#                 answer_content += chunk.output.choices[0].message.content[0]["text"]

#     # If you need to print the full reasoning process and complete response, uncomment the following lines
    # print("=" * 20 + "Full Reasoning Process" + "=" * 20 + "\n")
    # print(f"{reasoning_content}")
    # print("=" * 20 + "Complete Response" + "=" * 20 + "\n")
    # print(f"{answer_content}")
    