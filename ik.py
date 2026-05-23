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

import ikpy.chain
import numpy as np

chain = ikpy.chain.Chain.from_urdf_file("piper.urdf")
target_position = [0.3, 0.1, 0.4] 
target_orientation = [0, 0, -1]
initial_pos = [0] * len(chain.links)

ik_solution = chain.inverse_kinematics(
target_position=target_position,
target_orientation=target_orientation,
initial_position=initial_pos
)
    
tolerance = 1e-5
    
for i, link in enumerate(chain.links):
    if link.bounds is None or len(link.bounds) != 2:
        continue
        
    lower_limit, upper_limit = link.bounds
    joint_angle = ik_solution[i]
    
    if joint_angle < (lower_limit - tolerance) or joint_angle > (upper_limit + tolerance):
        print("Out of bounds")
        
print("Found solution within bounds:", np.round(ik_solution, 3))

# --- Execution ---

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