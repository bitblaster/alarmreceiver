#!/usr/bin/python
# -*- coding: utf-8 -*- 

import SocketServer
import socket
from datetime import datetime
import time
import httplib
import logging

# Config section
config={}
config['pi_server_url']       = "localhost:8444"
config['encrypt_iv']          = "12345678"
config['encrypt_passphrase']  = "1234567890abcdef"
config['groupsToSwitchOffWhenActive'] = ['1']
config['groupsToSwitchOnWhenDeactive'] = ['1']

LOG_PATH="/var/log/alarmReceiver.log"
logFormatter = logging.Formatter("%(asctime)s [%(threadName)-12.12s] [%(levelname)-5.5s]  %(message)s")

fileHandler = logging.FileHandler(LOG_PATH)
fileHandler.setFormatter(logFormatter)
logging.getLogger().addHandler(fileHandler)

consoleHandler = logging.StreamHandler()
consoleHandler.setFormatter(logFormatter)
logging.getLogger().addHandler(consoleHandler)

logging.getLogger().setLevel(logging.DEBUG)

ID_STRING='"SIA-DCS"'

DISABLE_SMS=True

ALWAYS="always"
IF_ACTIVE="active"
EMAIL_FALLBACK="fallback"
NEVER="never"

alarmPattern = re.compile(r"\[#[0-9]{6}\|....(..)[0-9]+\^(.+)\^")

class AlarmTCPHandler(SocketServer.BaseRequestHandler):
    """
    The RequestHandler class for our server.

    It is instantiated once per connection to the server, and must
    override the handle() method to implement communication to the
    client.
    """
    def __init__(self):
        self.alarmManager = AlarmManager()

    def handle(self):
        # self.request is the TCP socket connected to the client
        line = self.request.recv(1024).strip()
        logging.info("Ricevuto messaggio:".format(self.client_address[0]))
        logging.info(line)
        try:
            pos = line.index(ID_STRING)
            inputMessage=line[pos:]
            if line[0:4] != CRCCalc(inputMessage):
                #raise Exception("CRC errato!")
                # Anche se da specifiche dovremmo ignorare il messaggio mandiamo un NAK così l'allarme ripete!
                timestamp = datetime.fromtimestamp(time.time()).strftime('_%H:%M:%S,%m-%d-%Y')
                response = '"NAK"0000L0R0A0[]' + timestamp
            else:
                seq = line[pos+len(ID_STRING) : pos+len(ID_STRING)+4]
                accountId =line[line.index('#') : line.index('[')]
                response = '"ACK"' + seq + 'L0' + accountId + '[]'
            header = ('%04x' % len(response)).upper()
            # L'allarme non controlla nemmeno il checksum e basterebbe mandargli questo:
            # CRC="@?00";
            # ... ma noi facciamo le cose per bene
            CRC = CRCCalc(response)
            response="\n" + CRC + header + response + "\r"
            logging.info("Rispondo: " + response)
            self.request.sendall(response)

            t = threading.Thread(target=self.alarmManager.manageAlarmMessage, args=[inputMessage])
            t.start()

        except Exception as inst:
            logging.info("Errore: " + str(inst) + "\nMessaggio ignorato")
    
    @staticmethod
    def CRCCalc(msg):
        CRC=0
        for letter in msg:
            temp=ord(letter)
            for j in range(0,8):
                temp ^= CRC & 1
                CRC >>= 1
                if (temp & 1) != 0:
                    CRC ^= 0xA001
                temp >>= 1
                
        return ('%x' % CRC).upper().zfill(4)

if __name__ == "__main__":
    HOST, PORT = "", 9505
    #HOST, PORT = "localhost", 9505

    logging.info((HOST, PORT))
    # Create the server, binding to localhost on port 9999
    SocketServer.TCPServer.allow_reuse_address = True
    server = SocketServer.TCPServer((HOST, PORT), AlarmTCPHandler)

    # Activate the server; this will keep running until you
    # interrupt the program with Ctrl-C
    server.serve_forever()
