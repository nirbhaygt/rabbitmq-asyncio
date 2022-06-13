import threading
import queue
import pika
import structlog


class PublisherAsync:

    def __init__(self, credentials, queue_config) -> None:
        self.connection = None
        self.channel = None
        self.logger = structlog.get_logger()
        self._stopping = False
        
        self.credentials = credentials
        self.queue_config = queue_config

        self._message_queue = queue.Queue(maxsize=10000)
        
    def start(self):
        self.logger.info("Starting Thread")
        self.thread = threading.Thread(target=self.run, daemon=False)
        self.thread.start()
        return self
    
    def run(self):
        try:
            credentials = pika.PlainCredentials(self.credentials.get('username'), self.credentials.get('password'))
            connection_parameters = pika.ConnectionParameters(host=self.credentials.get('host'), port=self.credentials.get('port'), credentials=credentials)
            self.connection = pika.SelectConnection(connection_parameters)
            try:
                def on_connection_close(*args, **kwargs):
                    self.logger.msg("Connection closed callback")

                def on_connection_open(connection):
                    self.logger.msg("Connection Open. Callback received")
                    self.connection.channel(on_open_callback=self.on_open)
                
                self.connection.add_on_open_callback(on_connection_open)
                self.connection.add_on_close_callback(on_connection_close)
                self.logger.msg("Starting IOLoop")
                self.connection.ioloop.start()
            except Exception as e:
                self.logger.msg(f"Exception: {e}")
                if self.connection.is_open:
                    self.connection.close()
                self.logger.msg("Connection Closed")
                self.connection.ioloop.start()
            except KeyboardInterrupt as e:
                self.logger.msg(f"Interrupt: {e}")
                self.connection.close()
                self.logger.msg("Connection Closed")
        except Exception as e:
            self.logger.info(e)

    def close(self) -> None:
        self._stopping = True
        if self.channel and self.channel.is_open:
            self.channel.close()
            self.logger.info("Channel closed")

        if not self.channel:
            self.logger.info("No channel exists to close")
        
        if not self.channel.is_open:
            self.logger.info("Channel is already closed")
        
        if self.connection and self.connection.is_open:
            self.connection.close()
        
        if self.thread.is_alive:
            self.thread.stop()
    
    def on_open(self, channel):
        channel.add_on_close_callback(self.on_close)
        self.logger.info("Channel Established")
        self.channel = channel
        self.configure()


    def on_close(self, channel, *args, **kwargs):
        print(channel, *args)
        self.logger.critical("Channel Closed")
        if not self._stopping:
            if self.connection.is_open:
                self.channel.open()

    def configure(self):
        on_exchange_declare = lambda x: self.schedule_messaging()
        self.channel.exchange_declare(self.queue_config.get('exchange'), 
                                      exchange_type=self.queue_config.get('exchange_type'), 
                                      passive=self.queue_config.get('exchange_passive', False),
                                      durable=self.queue_config.get('exchange_durable', False),
                                      auto_delete=self.queue_config.get('exchange_auto_delete', False),
                                      callback=on_exchange_declare)

    def schedule_messaging(self):
        if self._message_queue.empty() == False:
            try:
                message_obj = self._message_queue.get()
                self.publish(message_obj.get('key'), 
                            message_obj.get('message'), 
                            message_obj.get('routing_key_prefix', None))
            except Exception as e:
                self.logger.error(f"Publishing Error: {e}")
        self.connection.ioloop.call_later(0.3, self.schedule_messaging)


    def publish(self, key, message, routing_key_prefix=None):
        if not self.channel or not self.channel.is_open:
            self.logger.info(f"Skipping Message: {message}")
            return

        routing_key = (routing_key_prefix or self.queue_config.get("routing_key_prefix") or "") + key
        self.logger.msg(f"Routing Key: {routing_key}")
        self.channel.basic_publish(self.queue_config.get("exchange"),
                                   routing_key,
                                   message)
    
    def push(self, key, message, routing_key_prefix=None):
        if not self.thread.is_alive:
            self.start()
        self._message_queue.put({
            'key': key,
            'message': message,
            'routing_key_prefix': routing_key_prefix
        })