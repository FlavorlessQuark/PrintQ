from ikpy.chain import Chain
from ikpy.link import OriginLink, URDFLink
import matplotlib.pyplot as plt

import numpy as np

piper_chain = Chain.from_urdf_file("piper_description.urdf")

target_position = [0.3, 0.1, 0.4] 
target_orientation = [0, 0, -1] 

initial_pos = [0] * len(piper_chain.links) 
ik_solution = piper_chain.inverse_kinematics(
    target_position=target_position,
    target_orientation=target_orientation,
    initial_position=initial_pos
)

print("rESULT", ik_solution)

fig, ax = plt.subplots(figsize=(8, 8))
ax = fig.add_subplot(111, projection='3d')

piper_chain.plot(ik_solution, ax, target=target_position)
plt.title("title")
ax.set_xlabel("X)")
ax.set_ylabel("Y")
ax.set_zlabel("Z")

ax.set_xlim(-0.6, 0.6)
ax.set_ylim(-0.6, 0.6)
ax.set_zlim(0.0, 0.8)


plt.savefig('my_plot.png')