import os
import subprocess

from mqttrpc import MQTTRPCResponseManager, dispatcher

from .collecting import collect_data_with_conf


@dispatcher.add_method
def diag():
    fullname = collect_data_with_conf()
    return {'basename': os.path.basename(fullname), 'fullname': fullname}


@dispatcher.add_method
def status():
    return "1"


def clear_directory():
    subprocess.run('rm /var/www/diag/*.zip', shell=True)


class TMQTTRPCServer(object):
    def __init__(self, client, driver_id):
        self.client = client
        self.driver_id = driver_id

    def on_mqtt_message(self, mosq, obj, msg):
        clear_directory()

        parts = msg.topic.split('/')
        service_id = parts[4]
        method_id = parts[5]
        client_id = parts[6]

        response = MQTTRPCResponseManager.handle(msg.payload, service_id, method_id, dispatcher)

        self.client.publish("/rpc/v1/%s/%s/%s/%s/reply" % (self.driver_id, service_id, method_id, client_id), response.json, False)

    def setup(self):
        for service, method in dispatcher.keys():
            self.client.publish("/rpc/v1/%s/%s/%s" % (self.driver_id, service, method), "1", retain=True)

            self.client.subscribe("/rpc/v1/%s/%s/%s/+" % (self.driver_id, service, method))

    def clean(self):
        for service, method in dispatcher.keys():
            self.client.publish("/rpc/v1/%s/%s/%s" % (self.driver_id, service, method), retain=True)

