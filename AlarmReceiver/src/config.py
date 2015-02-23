#!/usr/bin/python
# -*- coding: utf-8 -*- 

#######################    Configuration reader    ########################
# It opens file /etc/alarmConfig to load program properties.              #
# That file should contain at least the following properties:             #
# - listen_port (for example listen_port=9505)                            #
# - pi_server_url (for example pi_server_url=192.168.0.10:8444            #
# - encrypt_iv (for example encrypt_iv=12345678)                          #
# - encrypt_passphrase (for example encrypt_passphrase=1234567890abcdef)  #
###########################################################################

import ConfigParser

class Config:
    config=None
    
    @staticmethod
    def get(key):
        if not Config.config:
            Config.config=ConfigParser.SafeConfigParser()
            Config.config.readfp(FakeSecHead(open('/etc/alarmConfig')))
            print Config.config.items('DEFAULT')
        
        return Config.config.get('DEFAULT', key)
    
    @staticmethod
    def getInt(key):
        return int(Config.get(key))
    
class FakeSecHead(object):
    def __init__(self, fp):
        self.fp = fp
        self.sechead = '[DEFAULT]\n'

    def readline(self):
        if self.sechead:
            try: 
                return self.sechead
            finally: 
                self.sechead = None
        else: 
            return self.fp.readline()