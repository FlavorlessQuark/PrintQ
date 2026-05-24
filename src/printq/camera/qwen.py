import dashscope
from dashscope import MultiModalConversation

class Qwen:
    KEY = "sk-5a54c58071af4e7781b714772fc7a233"

    def __init__(self):
        pass

    # def get_print_status(self, b64str):
    #     import os
    #     dashscope.base_http_api_url = "https://dashscope-intl.aliyuncs.com/api/v1"

    #     messages = [
    #         {
    #             "role": "user",
    #             "content": [
    #                 {"image": f"data:image/jpeg;base64,{b64str}"},
    #                 {"text": "You are a helpful assistant for checking the quality of 3D prints. Please analyze the image and provide feedback on the print quality in a short sentence.\
    #                  If the print quality is good, just say 'The print quality looks good.' If there are issues, briefly describe them.\
    #                  Note that  bad print quality may include issues such as layer shifting, stringing, under-extrusion, over-extrusion, warping, or poor adhesion."}
    #             ]
    #         }
    #     ]

    #     response = MultiModalConversation.call(
    #         # If environment variable is not configured, replace the line below with: api_key="sk-xxx",
    #         api_key=self.KEY,
    #         model="qwen3-vl-flash",  # Here we use qvq-max as an example; you can change the model name as needed.
    #         messages=messages,
    #         stream=True,
    #     )

    #     reasoning_content = ""
    #     answer_content = ""
    #     is_answering = False

    #     print("=" * 20 + "Reasoning Process" + "=" * 20)

    #     for chunk in response:
    #         message = chunk.output.choices[0].message
    #         reasoning_content_chunk = message.get("reasoning_content", None)
    #         if (chunk.output.choices[0].message.content == [] and
    #             reasoning_content_chunk == ""):
    #             pass
    #         else:
    #             # If current part is reasoning process
    #             # if reasoning_content_chunk != None and chunk.output.choices[0].message.content == []:
    #             #     print(chunk.output.choices[0].message.reasoning_content, end="")
    #             #     reasoning_content += chunk.output.choices[0].message.reasoning_content
    #             # # If current part is response
    #             if chunk.output.choices[0].message.content != []:
    #                 if not is_answering:
    #                     print("\n" + "=" * 20 + "Complete Response" + "=" * 20)
    #                     is_answering = True
    #                 print(chunk.output.choices[0].message.content[0]["text"], end="")
    #                 answer_content += chunk.output.choices[0].message.content[0]["text"]

    #     # If you need to print the full reasoning process and complete response, uncomment the following lines
    #     print("=" * 20 + "Full Reasoning Process" + "=" * 20 + "\n")
    #     print(f"{reasoning_content}")
    #     print("=" * 20 + "Complete Response" + "=" * 20 + "\n")
    #     print(f"{answer_content}")

    def get_print_status(self, b64str):
        from openai import OpenAI
        import os

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