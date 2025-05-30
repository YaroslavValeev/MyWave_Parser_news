import asyncio
import functools

def rate_limited(max_per_second):
    min_interval = 1.0 / float(max_per_second)
    def decorate(func):
        last_time = [0.0]
        @functools.wraps(func)
        async def rate_limited_function(*args, **kwargs):
            elapsed = asyncio.get_event_loop().time() - last_time[0]
            left = min_interval - elapsed
            if left > 0:
                await asyncio.sleep(left)
            ret = await func(*args, **kwargs)
            last_time[0] = asyncio.get_event_loop().time()
            return ret
        return rate_limited_function
    return decorate
