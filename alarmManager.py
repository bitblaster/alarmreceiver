#!/usr/bin/python
# -*- coding: utf-8 -*- 

import sys
import logging
import threading
import re
import time
#import smtplib
#import httplib
import urllib
import requests
import base64
#from Crypto.Cipher import Blowfish
from email.mime.text import MIMEText
import paho.mqtt.client as mqtt


DISABLE_SMS=False

DISINSERIMENTO = 0
INSERIMENTO_TOTALE = 1
INSERIMENTO_PARZIALE = 2

class Config:
    config=None
    
    @staticmethod
    def load(configFile):
        import configparser
        Config.config=configparser.ConfigParser()
        print('Loading config file ' + configFile)
        Config.config.read_file(open(configFile))
        #print(Config.config.items('DEFAULT'))

    @staticmethod
    def get(key, defaultValue=None):
        if not Config.config:
           raise Exception('No config file loaded!')
        
        try:
            return Config.config.get('DEFAULT', key)
        except Exception as e:
            if defaultValue is None:
                raise e
            return defaultValue
    
    @staticmethod
    def getInt(key, defaultValue=None):
        try:
            return int(Config.get(key))
        except Exception as e:
            if defaultValue is None:
                raise e
            return defaultValue
    
    @staticmethod
    def getArray(key, splitChar = ','):
        return [x.strip() for x in Config.get(key, '').split(splitChar)]
    
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

class AlarmManager:
    alarmPattern = re.compile(r"\[#[0-9]{6}\|....(..)([0-9]+)\^?([^\^]*)\^?\]")
        
    def __init__(self):
        self.alarmActive = False
        self.threadLock = threading.Lock()
        self.reactions = {            
            # Non definito
            "UX" : {"subject": "Non definito", "execute": None},
            
            # Set
            "BA" : {"subject": "ALLARME INTRUSIONE", "execute": self.sendCriticalTelegramMessage},
            "TA" : {"subject": "SABOTAGGIO", "execute": self.sendTelegramMessage},
            "BB" : {"subject": "Esclusione", "execute": self.sendTelegramMessage},
            "CL" : {"subject": "Inserimento totale", "execute": self.inserimentoTotale},
            "NL" : {"subject": "Inserimento parziale", "execute": self.inserimentoParziale},
            "BC" : {"subject": "Reset memoria", "execute": self.sendTelegramMessage},
            "JP" : {"subject": "Riconoscimento codice/chiave", "execute": self.sendTelegramMessage},
            "XT" : {"subject": "Resistenza interna batteria", "execute": self.sendTelegramMessage},
            "YM" : {"subject": "Corto circuito/disconnessione batteria", "execute": self.sendTelegramMessage},
            "YT" : {"subject": "Batteria inefficiente", "execute": self.sendTelegramMessage},
            "AT" : {"subject": "Mancanza alimentazione", "execute": self.sendCriticalTelegramMessage},
            "EM" : {"subject": "Scomparsa dispositivo", "execute": self.sendTelegramMessage},
            "DD" : {"subject": "Codice/chiave errati", "execute": self.sendTelegramMessage},
            "LB" : {"subject": "Ingresso programmazione", "execute": self.sendTelegramMessage},
            "OU" : {"subject": "Malfunzionamento uscita", "execute": self.sendCriticalTelegramMessage},
            
            # Reset
            "BR" : {"subject": "Ripristino allarme intrusione", "execute": self.sendTelegramMessage},
            "TR" : {"subject": "Ripristino sabotaggio", "execute": self.sendTelegramMessage},
            "BU" : {"subject": "Ripristino esclusione", "execute": self.sendTelegramMessage},
            "OP" : {"subject": "Disinserimento", "execute": self.disinserimento},
            "XR" : {"subject": "Ripristino resistenza interna batteria", "execute": self.sendTelegramMessage},
            "YR" : {"subject": "Ripristino batteria", "execute": self.sendTelegramMessage},
            "AR" : {"subject": "Ripristino alimentazione", "execute": self.sendCriticalTelegramMessage},
            "EN" : {"subject": "Ripristino scomparsa dispositivo", "execute": self.sendTelegramMessage},
            "DR" : {"subject": "Ripristino codice/chiave errati", "execute": self.sendTelegramMessage},
            "LX" : {"subject": "Uscita programmazione", "execute": self.sendTelegramMessage},
            "OV" : {"subject": "Ripristino malfunzionamento uscita", "execute": self.sendCriticalTelegramMessage}
        }

    def manageAlarmMessage(self, msg):
        m = AlarmManager.alarmPattern.search(msg)
        if m:
            tipo = m.group(1)
            param = m.group(2)
            desc = re.sub(r'\s\s+',' ', m.group(3)).strip()
            logging.info("Tipo evento: " + tipo + ", param: " + param + ", testo: " + desc)
            
            if tipo in self.reactions:
                reaction = self.reactions[tipo]
                subject = reaction["subject"]
                message = subject + ": " + desc
                executeMethod = reaction["execute"]
                if executeMethod:
                    # # Get lock to synchronize threads
                    # logging.debug("Acquiring lock...")
                    # self.threadLock.acquire(timeout=5.0)
                    # if not locked:
                    #     # Non ho ottenuto il lock → salto il blocco critico
                    #     logging.error("Unable to obtain the lock, skipping event")
                    #     return
                    # 
                    # try:
                    #     executeMethod(subject, message, param)
                    # finally:
                    #     # Free lock to release next thread
                    #     self.threadLock.release()
                    #     logging.debug("Lock released!")
                    try:
                        executeMethod(subject, message, param)
                    except Exception as e:
                        logging.error("Error executing handler: " + e)
            else:
                logging.warn(f"Evento sconosciuto: {tipo}: {desc}")
            
            return
    
    def inserimentoTotale(self, subject, message, param):
        self.alarmActive = INSERIMENTO_TOTALE
        self.updateHAStatus("INSERIMENTO_TOTALE")
        self.sendTelegramMessage(subject)
        time.sleep(1)
       
    def inserimentoParziale(self, subject, message, param):
        self.alarmActive = INSERIMENTO_PARZIALE
        self.updateHAStatus("INSERIMENTO_PARZIALE")
        self.sendTelegramMessage(subject)
    
    def disinserimento(self, subject, message, param):
        self.alarmActive = DISINSERIMENTO
        self.updateHAStatus("DISINSERIMENTO")
        self.sendTelegramMessage(subject)
        
    def sendTelegramMessage(self, msg, critical=False):
        logging.info("Invio telegram: " + msg)
        requestUrl = Config.get('telegram_critical_message_url' if critical else 'telegram_message_url') + urllib.parse.quote_plus(msg)
        res = requests.get(requestUrl)
        if res.status_code != 200:
            logging.error("Error executing request, status: " + str(res.status_code) + ", request: " + requestUrl)
   
    def sendCriticalTelegramMessage(self, msg):
        self.sendTelegramMessage(msg, True)

    def updateHAStatus(self, stato):
        mqttClient = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        mqttClient.username_pw_set(Config.get('mqtt_user'), Config.get('mqtt_password'))
        mqttClient.connect(Config.get('mqtt_broker'), Config.getInt('mqtt_port'), 60)
        mqttClient.publish(Config.get('mqtt_topic'), stato)
        mqttClient.disconnect()

if __name__ == "__main__":
    logging.info('-------- AlarmManager TEST startup --------')
    Config.load('/etc/alarmReceiver.conf')

    alarmManager = AlarmManager()
    #s = '"SIA-DCS"0091L0#001234[#001234|Nri0CL0]_06:43:58,02-15-2015'       # inserimento
    s = '"SIA-DCS"0714L0#001234[#001234|Nri0OP4^Sirena Interna  ^]_11:47:38,11-13-2025'  # disinserimento
    alarmManager.manageAlarmMessage(s)
