from openai import OpenAI

class Qwen:
    KEY = "sk-5a54c58071af4e7781b714772fc7a233"

    def __init__(self):
        pass

    def get_print_status(self, b64str):

        client = OpenAI(
        api_key=self.KEY,
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        )

        completion = client.chat.completions.create(
        model="qwen3-vl-flash",
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url":f"data:image/jpeg;base64,{b64str}"}
                    },
                    {
                        "type": "text",
                        "text": "You are a helpful assistant for checking the quality of 3D prints. Please analyze the image and provide feedback on the print quality in a short sentence.\
                      If there are issues, briefly describe them. Issues \
                     Note that  bad print quality may include issues such as layer shifting, stringing, under-extrusion, over-extrusion, warping, or poor adhesion.\
                        If the print looks stringy, crooked, has visible gaps or chunks of filament missing, it likely has quality issues. If the print looks smooth, well-adhered to the bed, and closely matches the expected shape, it likely has good quality.\
                        Your message MUST start with Y if the print was good and N if the print  was not good, then, write the rest of the message as normal"
                     }
                ]
            }
        ]
        )
        print(completion.choices[0].message.content)
        return completion.choices[0].message.content[0] =='Y', completion.choices[0].message.content[2:]