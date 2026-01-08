# bot_api.py
class BotBridge:
    def __init__(self):
        self._bot = None

    def set_bot(self, bot):
        self._bot = bot

    async def announce_link(self, channel_id: int, message: str):
        if self._bot is None:
            print("[Bridge] Bot not set (cannot announce)")
            return

        channel = self._bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self._bot.fetch_channel(channel_id)
            except Exception as e:
                print("[Bridge] fetch_channel failed:", e)
                return

        try:
            await channel.send(message)
        except Exception as e:
            print("[Bridge] channel.send failed:", e)

bridge = BotBridge()
