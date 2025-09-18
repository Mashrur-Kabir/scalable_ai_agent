from aiocache import Cache
from aiocache.serializers import JsonSerializer
import os

CACHE_TTL = int(os.getenv("CACHE_TTL", "3600"))

# Simple in-memory cache (LRU-like) using aiocache.simple memory backend
cache = Cache(Cache.MEMORY, ttl=CACHE_TTL, serializer=JsonSerializer())