"""One approved port connection, independent of the device's shared identity."""
import asyncio
from dataclasses import dataclass

from .proxy import Proxy


@dataclass
class Connection:
    id: str
    proxy: Proxy

    async def start(self, local_port):
        try:
            await asyncio.wait_for(self.proxy.client.start(), 10)
            self.proxy.spawn(self.proxy.maintain())
            if self.proxy.initiator:
                await self.proxy.add_forward(self.proxy.protocol, local_port)
        except BaseException:
            await self.proxy.close()
            raise

    async def close(self):
        await self.proxy.close()

    def status(self):
        return dict(id=self.id, **self.proxy.status())
