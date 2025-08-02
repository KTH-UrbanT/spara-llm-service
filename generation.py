from src.redis.redis_manager import RedisQueueManager

if __name__ == "__main__":
    RedisQueueManager().event_listener()
