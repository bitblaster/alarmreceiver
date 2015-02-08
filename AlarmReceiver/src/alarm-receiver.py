#!/usr/bin/python
# -*- coding: utf-8 -*- 

import SocketServer
import socket
from datetime import datetime
import time

import logging
import threading
import re
import smtplib
from email.mime.text import MIMEText

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

alarmActive=False
threadLock = threading.Lock()

# CL-OP -> Inserimento totale
# NL-OP -> Inserimento parziale
# BC -> Reset memoria
# JP -> Riconoscimento codice/chiave
# XT-XR -> Resistenza interna batteria
# YM-YR -> Corto circuito/disconnessione batteria
# YT-YR -> Batteria inefficiente
# AT-AR -> Mancanza alimentazione
# EM-EN -> Scomparsa dispositivo
# DD-DR -> Codice/chiave errati
# LB-LX -> Ingresso programmazione
# OU-OV -> Malfunzionamento uscita
errorCodes = {
      # Non definito
      "UX" : {"desc": "Non definito", "sendSms": NEVER},
      
      # Set
      "BA" : {"desc": "ALLARME INTRUSIONE", "sendSms": ALWAYS},
      "TA" : {"desc": "SABOTAGGIO", "sendSms": IF_ACTIVE},
      "BB" : {"desc": "Esclusione", "sendSms": NEVER},
      "CL" : {"desc": "Inserimento totale", "sendSms": NEVER},
      "NL" : {"desc": "Inserimento parziale", "sendSms": NEVER},
      "BC" : {"desc": "Reset memoria", "sendSms": NEVER},
      "JP" : {"desc": "Riconoscimento codice/chiave", "sendSms": NEVER},
      "XT" : {"desc": "Resistenza interna batteria", "sendSms": IF_ACTIVE},
      "YM" : {"desc": "Corto circuito/disconnessione batteria", "sendSms": IF_ACTIVE},
      "YT" : {"desc": "Batteria inefficiente", "sendSms": IF_ACTIVE},
      "AT" : {"desc": "Mancanza alimentazione", "sendSms": IF_ACTIVE},
      "EM" : {"desc": "Scomparsa dispositivo", "sendSms": IF_ACTIVE},
      "DD" : {"desc": "Codice/chiave errati", "sendSms": ALWAYS},
      "LB" : {"desc": "Ingresso programmazione", "sendSms": IF_ACTIVE},
      "OU" : {"desc": "Malfunzionamento uscita", "sendSms": EMAIL_FALLBACK},
      
      # Reset
      "BR" : {"desc": "Ripristino allarme intrusione", "sendSms": ALWAYS},
      "TR" : {"desc": "Ripristino sabotaggio", "sendSms": ALWAYS},
      "BU" : {"desc": "Ripristino esclusione", "sendSms": NEVER},
      "OP" : {"desc": "Disinserimento", "sendSms": NEVER},
      "XR" : {"desc": "Ripristino resistenza interna batteria", "sendSms": IF_ACTIVE},
      "YR" : {"desc": "Ripristino batteria", "sendSms": IF_ACTIVE},
      "AR" : {"desc": "Ripristino alimentazione", "sendSms": IF_ACTIVE},
      "EN" : {"desc": "Ripristino scomparsa dispositivo", "sendSms": IF_ACTIVE},
      "DR" : {"desc": "Ripristino codice/chiave errati", "sendSms": ALWAYS},
      "LX" : {"desc": "Uscita programmazione", "sendSms": IF_ACTIVE},
      "OV" : {"desc": "Ripristino malfunzionamento uscita", "sendSms": EMAIL_FALLBACK}
}

class AlarmTCPHandler(SocketServer.BaseRequestHandler):
    """
    The RequestHandler class for our server.

    It is instantiated once per connection to the server, and must
    override the handle() method to implement communication to the
    client.
    """

    def handle(self):
        # self.request is the TCP socket connected to the client
        line = self.request.recv(1024).strip()
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
            self.request.sendall(response)

            #AlarmTCPHandler.manageAlarmMessage(inputMessage)
            t = threading.Thread(target=AlarmTCPHandler.manageAlarmMessage, args=[inputMessage])
            t.start()

        except Exception as inst:
            logging.info("Errore: " + str(inst) + "\nMessaggio ignorato")
    
    @staticmethod
    def manageAlarmMessage(msg):
        m = alarmPattern.search(msg)
        if m:
            tipo = m.group(1)
            desc = m.group(2).strip()
            logging.info("Tipo evento: " + tipo + ", testo: " + desc)
            
            if tipo == "CL" or tipo == "NL":
                alarmActive = True
            elif tipo == "OP":
                alarmActive = False
                
            errorCode = errorCodes[tipo]
            if errorCode:
                msgString = errorCode["desc"] + ": " + desc
            else:
                msgString = "Evento sconosciuto: " + errorCode[0] + ": " + desc
            
            smsPolicy = errorCode["sendSms"]
            
            if smsPolicy == ALWAYS or smsPolicy == IF_ACTIVE and alarmActive:
                AlarmTCPHandler.sendSms(msgString)
                
            mailSent = AlarmTCPHandler.sendEmail(errorCode["desc"], msgString)
            if not mailSent and smsPolicy == EMAIL_FALLBACK:
                AlarmTCPHandler.sendSms(msgString)
                
    @staticmethod
    def sendEmail(subject, msg):
        # Get lock to synchronize threads
        logging.debug("Acquiring lock...")
        threadLock.acquire()
        
        logging.info("Invio email: " + msg)
        try:
            msg = MIMEText(msg)

            mailFrom = "Allarme Casa <videomozzi@gmail.com>"
            mailTo = "Roberto Mozzicato <bitblasters@gmail.com>"
            msg['Subject'] = subject
            msg['From'] = mailFrom
            msg['To'] = mailTo
    
            server = smtplib.SMTP('smtp.gmail.com:587')
            server.starttls()
            server.login("videomozzi","pwdmarcia*123")
            server.sendmail(mailFrom, [mailTo], msg.as_string())
            server.quit()
            
            return True
        except Exception as e:
            logging.error("Errore durante l'invio dell'email: " + str(e))
            return False
        finally:        
            # Free lock to release next thread
            threadLock.release()
            logging.debug("Lock released!")
        
    @staticmethod
    def sendSms(msg):
        logging.info("Invio sms: " + msg)
        if DISABLE_SMS:
            logging.info("sms disabled")
            return
        
        # Get lock to synchronize threads
        logging.debug("Acquiring lock...")
        threadLock.acquire()
        
        # Free lock to release next thread
        threadLock.release()
        logging.debug("Lock released!")

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
