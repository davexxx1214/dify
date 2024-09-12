import json
import re
import plugins
from bridge.reply import Reply, ReplyType
from bridge.context import ContextType
from channel.chat_message import ChatMessage
from plugins import *
from common.log import logger
from common.tmp_dir import TmpDir
from common.expired_dict import ExpiredDict
import requests
import cairosvg
from PIL import Image
from io import BytesIO

import io
import os
import uuid
from glob import glob


@plugins.register(
    name="dify",
    desire_priority=2,
    desc="A plugin to call Dify API",
    version="0.0.1",
    author="davexxx",
)

class dify(Plugin):
    def __init__(self):
        super().__init__()
        try:
            curdir = os.path.dirname(__file__)
            config_path = os.path.join(curdir, "config.json")
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            else:
                # 使用父类的方法来加载配置
                self.config = super().load_config()

                if not self.config:
                    raise Exception("config.json not found")
            
            # 设置事件处理函数
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
            # 从配置中提取所需的设置
            self.api_key = self.config.get("api_key","")
            self.dify_prefix = self.config.get("dify_prefix","")

            self.params_cache = ExpiredDict(500)

            # 初始化成功日志
            logger.info("[dify] inited.")
        except Exception as e:
            # 初始化失败日志
            logger.warn(f"dify init failed: {e}")

    def on_handle_context(self, e_context: EventContext):
        context = e_context["context"]
        if context.type not in [ContextType.TEXT, ContextType.SHARING, ContextType.FILE, ContextType.IMAGE]:
            return
        msg: ChatMessage = e_context["context"]["msg"]
        user_id = msg.from_user_id
        content = context.content

        # 将用户信息存储在params_cache中
        if user_id not in self.params_cache:
            self.params_cache[user_id] = {}
            self.params_cache[user_id]['text_prompt'] = None

            logger.debug('Added new user to params_cache. user id = ' + user_id)

        if e_context['context'].type == ContextType.TEXT:
            if content.startswith(self.dify_prefix):
                pattern = self.dify_prefix + r"\s(.+)"
                match = re.match(pattern, content)
                if match:  # 匹配上了dify的指令
                    text_prompt = content[len(self.dify_prefix):].strip()
                    self.params_cache[user_id]['text_prompt'] = text_prompt
                    self.call_dify_service(user_id, e_context)
                else:
                    tip = f"💡欢迎使用汉字新解，指令格式为:\n\n{self.dify_prefix} + 希望解释的词语\n例如：{self.dify_prefix} 中国男足"
                    reply = Reply(type=ReplyType.TEXT, content=tip)
                    e_context["reply"] = reply
                    e_context.action = EventAction.BREAK_PASS
    
    def generate_unique_output_directory(self, base_dir):
        """Generate a unique output directory using a UUID."""
        unique_dir = os.path.join(base_dir, str(uuid.uuid4()))
        os.makedirs(unique_dir, exist_ok=True)
        return unique_dir

    def call_dify_service(self, user_id, e_context):
        prompt = self.params_cache[user_id]['text_prompt']
        logger.info("call_dify_service, prompt = {prompt}, user_id = {user_id}")

        imgpath = TmpDir().path() + "dify" + str(uuid.uuid4()) + ".png" 

        try:
            url = "https://api.dify.ai/v1/chat-messages"
            headers = {
                'Authorization': 'Bearer {self.api_key}',
                'Content-Type': 'application/json',
            }

            data = {
                "inputs": {},
                "query": prompt,
                "response_mode": "blocking",
                "user": user_id
            }

            response = requests.post(url, headers=headers, data=json.dumps(data))

            response_json = json.loads(response.text)
            svg_data = response_json['answer']
            svg_data = svg_data.lstrip('svg\n')

            png_data = cairosvg.svg2png(bytestring=svg_data.encode('utf-8'))
            # 保存 PNG 数据为文件
            with open(imgpath, "wb") as png_file:
                png_file.write(png_data)
            logger.info(f"image saved to : {imgpath}")

            rt = ReplyType.IMAGE
            image = self.img_to_png(imgpath)
            if image is False:
                rc= "服务暂不可用"
                rt = ReplyType.TEXT
                reply = Reply(rt, rc)
                logger.error("[dify] service exception")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
            else:
                rc = image
                reply = Reply(rt, rc)
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
            return

        except Exception as e:
            logger.error("call dify api error: {}".format(e))
            rt = ReplyType.TEXT
            rc = f"服务暂不可用,错误信息: {e}"
            reply = Reply(rt, rc)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return
        
    def send_reply(self, reply, e_context: EventContext, reply_type=ReplyType.TEXT):
        if isinstance(reply, Reply):
            if not reply.type and reply_type:
                reply.type = reply_type
        else:
            reply = Reply(reply_type, reply)
        channel = e_context['channel']
        context = e_context['context']
        # reply的包装步骤
        rd = channel._decorate_reply(context, reply)
        # reply的发送步骤
        return channel._send_reply(context, rd)
    
    def img_to_png(self, file_path):
        try:
            image = io.BytesIO()
            idata = Image.open(file_path)  # 使用文件路径打开图像
            idata = idata.convert("RGBA")  # 转换为RGBA模式以保持PNG的透明度
            idata.save(image, format="PNG")  # 指定保存格式为PNG
            image.seek(0)
            return image
        except Exception as e:
            logger.error(e)
            return False