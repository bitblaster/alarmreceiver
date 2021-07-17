#!/usr/bin/python3
# -*- coding: utf-8 -*- 

import sys, os
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), 'pythonCommon'))

from alarmManager import Config
from alarmManager import AlarmManager
import socketserver
from datetime import datetime
import time
import logging
import threading

if len(sys.argv) > 1 and sys.argv[1] == "1":
    adbPath = "/opt/android-sdk-linux_x86/platform-tools/adb"
else:
    adbPath = "/opt/adb"
    
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

alarmManager = AlarmManager(adbPath)

class AlarmTCPHandler(socketserver.BaseRequestHandler):
    """
    The RequestHandler class for our server.

    It is instantiated once per connection to the server, and must
    override the handle() method to implement communication to the
    client.
    """

    def handle(self):
        # self.request is the TCP socket connected to the client
        line = self.request.recv(1024).strip().decode('ascii')
        logging.info("Ricevuto messaggio:".format(self.client_address[0]))
        logging.info(line)
        try:
            pos = line.index(ID_STRING)
            inputMessage=line[pos:]
            if line[0:4] != AlarmTCPHandler.CRCCalc(inputMessage):
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
            CRC = AlarmTCPHandler.CRCCalc(response)
            response="\n" + CRC + header + response + "\r"
            logging.info("Rispondo: " + response)
            self.request.sendall(response.encode('ascii'))

            t = threading.Thread(target=alarmManager.manageAlarmMessage, args=[inputMessage])
            t.start()
        except Exception as inst:
            logging.info("Errore: " + str(inst) + "\nMessaggio ignorato")
    
    @staticmethod
    def CRCCalc(msg):
        CRC=0
        for letter in msg:
            temp=ord(letter)
            for j in range(0,8):  # @UnusedVariable
                temp ^= CRC & 1
                CRC >>= 1
                if (temp & 1) != 0:
                    CRC ^= 0xA001
                temp >>= 1
                
        return ('%x' % CRC).upper().zfill(4)

if __name__ == "__main__":
    Config.load('/etc/alarmReceiver.conf')
    
    if len(sys.argv) > 1 and sys.argv[1] == "-t":
        logging.info('-------- AlarmReceiver TEST startup --------')
        
        print ("dovrebbero essere inviati messaggi nel seguente ordine:\n"
                "Inserimento totale (solo email)\n"
                "Sabotaggio (email + SMS)\n"
                "Allarme intrusione (email + SMS)\n"
                "Disinserimento (solo email)\n"
                "Sabotaggio (solo email)\n"
        )
        messages = [
            #'"SIA-DCS"0091L0#001234[#001234|Nri0CL0]_06:43:58,02-15-2015',
            #'"SIA-DCS"0091L0#001234[#001234|Nri0TA0]_06:43:58,02-15-2015',
            #'"SIA-DCS"0091L0#001234[#001234|Nri0BA0^IR Ingresso     Appartamento    ^]_06:43:58,02-15-2015',
            #'"SIA-DCS"0091L0#001234[#001234|Nri0OP0]_06:43:58,02-15-2015',
            #'"SIA-DCS"0091L0#001234[#001234|Nri0TA0]_06:43:58,02-15-2015'
        ]
        for m in messages:
            alarmManager.manageAlarmMessage(m)
            time.sleep(20)            
    else:
        logging.info('-------- AlarmReceiver startup --------')
        alarmManager.sendTelegramMessage('-------- AlarmReceiver startup --------')
        # Primo parametro vuoto per esporre il socket su tutte le interfacce di rete
        HOST, PORT = "", Config.getInt("server_port")
        #HOST, PORT = "localhost", 9505

        
        logging.info((HOST, PORT))
        # Create the server, binding to localhost on port 9999
        socketserver.TCPServer.allow_reuse_address = True
        server = socketserver.TCPServer((HOST, PORT), AlarmTCPHandler)

        # Activate the server; this will keep running until you
        # interrupt the program with Ctrl-C
        server.serve_forever()
