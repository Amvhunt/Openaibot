# -*- coding: utf-8 -*-
# @Time    : 9/22/22 11:04 PM
# @FileName: Controller.py.py
# @Software: PyCharm
# @Github: purofle
import asyncio
import time
from collections import deque
from typing import Union, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from graia.amnesia.message import MessageChain
from graia.ariadne import Ariadne
from graia.ariadne.connection.config import config, HttpClientConfig, WebsocketClientConfig
from graia.ariadne.message import Source, Quote
from graia.ariadne.message.element import Voice, Plain
from graia.ariadne.message.parser.twilight import UnionMatch
from graia.ariadne.model import Group, Member, Friend, MemberPerm
from graiax import silkcoder
from loguru import logger

from App import Event
from utils import Setting
from utils.Chat import Utils
from utils.Data import create_message, User_Message, PublicReturn, DefaultData
from utils.Frequency import Vitality


async def set_cron(funcs, second: int):
    """
    启动一个异步定时器
    :param funcs: 回调函数
    :param second: 秒数
    :return:
    """
    tick_scheduler = AsyncIOScheduler()
    tick_scheduler.add_job(funcs, trigger='interval', max_instances=10, seconds=second)
    tick_scheduler.start()


time_interval = 60 * 5
# 使用 deque 存储请求时间戳
request_timestamps = deque()


def get_user_message(
        message: MessageChain,
        member: Union[Member, Friend],
        group: Optional[Group] = None) -> User_Message:
    return create_message(
        state=101,
        user_id=member.id,  # qq 号
        user_name=member.name if isinstance(member, Member) else member.nickname,
        group_id=group.id if group else member.id,
        text=str(message),
        group_name=group.name if group else "Group"
    )


class BotRunner:
    def __init__(self, _config):
        self.config = _config

    def botCreate(self):
        if not self.config.verify_key:
            return None
        return Ariadne(config(self.config.account, self.config.verify_key, HttpClientConfig(host=self.config.http_host),
                              WebsocketClientConfig(host=self.config.ws_host)))

    def run(self, pLock=None):
        bot = self.botCreate()
        if not bot:
            logger.info("APP:QQ Bot Close")
            return None
        logger.success("APP:QQ Bot Start")

        @bot.broadcast.receiver("FriendMessage", dispatchers=[UnionMatch("/about", "/start", "/help")])
        async def starter(app: Ariadne, message: MessageChain, friend: Friend, source: Source):
            logger.info(message.content)
            message = str(message)
            if message == "/about":
                await app.send_message(friend, await Event.About(self.config), quote=source)
            elif message == "/start":
                await app.send_message(friend, await Event.Start(self.config), quote=source)
            elif message == "/help":
                await app.send_message(friend, await Event.Help(self.config), quote=source)

        async def get_message_chain(_hand: User_Message):
            request_timestamps.append(time.time())

            if not _hand.text.startswith("/"):
                _hand.text = f"/chat {_hand.text}"
            # _friends_message = await Event.Text(_hand, self.config)
            _friends_message = await Event.Friends(_hand, self.config)

            if not _friends_message.status:
                return None

            _caption = f"{_friends_message.reply}\n{self.config.INTRO}"
            if _friends_message.voice:
                # 转换格式
                voice = await silkcoder.async_encode(_friends_message.voice, audio_format="ogg")
                message_chain = MessageChain([Voice(data_bytes=voice)])
            elif _friends_message.reply:
                message_chain = MessageChain([Plain(_caption)])
            else:
                message_chain = MessageChain([Plain(_friends_message.msg)])
            return message_chain

        # "msg" @ RegexMatch(r"\/\b(chat|voice|write|forgetme|remind)\b.*")

        @bot.broadcast.receiver("FriendMessage")
        async def chat(app: Ariadne, msg: MessageChain, friend: Friend, source: Source):
            _hand = get_user_message(msg, member=friend, group=None)
            _read_id = friend.id
            if _read_id in self.config.master:
                _reply = await Event.MasterCommand(user_id=_read_id, Message=_hand, config=self.config, pLock=pLock)
                if _reply:
                    await app.send_message(friend, "".join(_reply), quote=source)

            message_chain = await get_message_chain(_hand)
            if message_chain:
                active_msg = await app.send_message(friend, message_chain, quote=source)
                # Utils.trackMsg(f"{_hand.from_chat.id}{active_msg.id}", user_id=_hand.from_user.id)

        @bot.broadcast.receiver("GroupMessage")
        async def group_chat(app: Ariadne,
                             msg: MessageChain,
                             quote: Optional[Quote],
                             member: Member,
                             group: Group,
                             source: Source):
            _hand = get_user_message(msg, member=member, group=group)
            _hand: User_Message
            get_request_frequency()
            started = False
            if _hand.text.startswith(("/chat", "/voice", "/write", "/forgetme", "/remind")):
                started = True
            elif _hand.text.startswith("/"):
                _is_admin = member.permission
                if _is_admin in [MemberPerm.Owner, MemberPerm.Administrator]:
                    _reply = await Event.GroupAdminCommand(Message=_hand, config=self.config, pLock=pLock)
                    if _reply:
                        message_chain = MessageChain([Plain("".join(_reply))])
                        await app.send_message(group, message_chain, quote=source)
            if quote:
                if str(Utils.checkMsg(
                        f"{_hand.from_chat.id}{source.id}")) == f"{_hand.from_user.id}":
                    if not _hand.text.startswith("/"):
                        _hand.text = f"/chat {_hand.text}"
                    started = True

            # 分发指令
            if _hand.text.startswith("/help"):
                await bot.send_message(group, await Event.Help(self.config))

            # 热力扳机
            if not started:
                _tigger_message = await Event.Tigger(_hand, self.config)
                if _tigger_message.status:
                    _GroupTigger = Vitality(group_id=_hand.from_chat.id)
                    _GroupTigger.tigger(Message=_hand, config=self.config)
                    _check = _GroupTigger.check(Message=_hand)
                    if _check:
                        _hand.text = f"/catch {_hand.text}"
                        started = True
            if started:
                request_timestamps.append(time.time())
                _friends_message = await Event.Group(_hand, self.config)
                _friends_message: PublicReturn

                _caption = f"{_friends_message.reply}\n{self.config.INTRO}"
                if _friends_message.voice:
                    # 转换格式
                    voice = await silkcoder.async_encode(_friends_message.voice, audio_format="ogg")
                    message_chain = MessageChain([Voice(data_bytes=voice)])
                elif _friends_message.reply:
                    message_chain = MessageChain([Plain(_caption)])
                else:
                    message_chain = MessageChain([Plain(_friends_message.msg)])
                if message_chain:
                    active_msg = await app.send_message(group, message_chain, quote=source)
                    Utils.trackMsg(f"QQ{_hand.from_chat.id}{active_msg.id}", user_id=_hand.from_user.id)

        def get_request_frequency():
            # 检查队列头部是否过期
            while request_timestamps and request_timestamps[0] < time.time() - time_interval:
                request_timestamps.popleft()
            # 计算请求频率
            request_frequency = len(request_timestamps)
            DefaultData().setAnalysis(qq=request_frequency)
            return request_frequency

        Setting.qqbot_profile_init()
        Ariadne.launch_blocking()
