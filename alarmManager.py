#!/usr/bin/python
# -*- coding: utf-8 -*- 

import sys
import logging
import threading
import re
import time
import smtplib
#import httplib
import urllib
import requests
import base64
from Crypto.Cipher import Blowfish
from email.mime.text import MIMEText
#from ADB import ADB

DISABLE_ANDROID_CMDS=True
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
        print(Config.config.items('DEFAULT'))

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
        
    def __init__(self, adbPath):
        #self.adb=ADB(adbPath)
        self.alarmActive = False
        self.threadLock = threading.Lock()
        self.reactions = {            
            # Non definito
            "UX" : {"subject": "Non definito", "execute": None},
            
            # Set
            "BA" : {"subject": "ALLARME INTRUSIONE", "execute": self.inviaSmsEdEmail},
            "TA" : {"subject": "SABOTAGGIO", "execute": self.inviaSmsSeInseritoEdEmail},
            "BB" : {"subject": "Esclusione", "execute": self.sendEmail},
            "CL" : {"subject": "Inserimento totale", "execute": self.inserimentoTotale},
            "NL" : {"subject": "Inserimento parziale", "execute": self.inserimentoParziale},
            "BC" : {"subject": "Reset memoria", "execute": self.sendEmail},
            "JP" : {"subject": "Riconoscimento codice/chiave", "execute": self.sendEmail},
            "XT" : {"subject": "Resistenza interna batteria", "execute": self.sendEmail},
            "YM" : {"subject": "Corto circuito/disconnessione batteria", "execute": self.sendEmail},
            "YT" : {"subject": "Batteria inefficiente", "execute": self.sendEmail},
            "AT" : {"subject": "Mancanza alimentazione", "execute": self.inviaSmsEdEmail},
            "EM" : {"subject": "Scomparsa dispositivo", "execute": self.sendEmail},
            "DD" : {"subject": "Codice/chiave errati", "execute": self.sendEmail},
            "LB" : {"subject": "Ingresso programmazione", "execute": self.sendEmail},
            "OU" : {"subject": "Malfunzionamento uscita", "execute": self.inviaSmsSeEmailNonFunziona},
            
            # Reset
            "BR" : {"subject": "Ripristino allarme intrusione", "execute": self.sendEmail},
            "TR" : {"subject": "Ripristino sabotaggio", "execute": self.sendEmail},
            "BU" : {"subject": "Ripristino esclusione", "execute": self.sendEmail},
            "OP" : {"subject": "Disinserimento", "execute": self.disinserimento},
            "XR" : {"subject": "Ripristino resistenza interna batteria", "execute": self.sendEmail},
            "YR" : {"subject": "Ripristino batteria", "execute": self.sendEmail},
            "AR" : {"subject": "Ripristino alimentazione", "execute": self.inviaSmsEdEmail},
            "EN" : {"subject": "Ripristino scomparsa dispositivo", "execute": self.sendEmail},
            "DR" : {"subject": "Ripristino codice/chiave errati", "execute": self.sendEmail},
            "LX" : {"subject": "Uscita programmazione", "execute": self.sendEmail},
            "OV" : {"subject": "Ripristino malfunzionamento uscita", "execute": self.inviaSmsSeEmailNonFunziona}
        }
        
    def manageAlarmMessage(self, msg):
        m = AlarmManager.alarmPattern.search(msg)
        if m:
            tipo = m.group(1)
            param = m.group(2)
            desc = re.sub('\s\s+',' ', m.group(3)).strip()
            logging.info("Tipo evento: " + tipo + ", param: " + param + ", testo: " + desc)
            
            if tipo in self.reactions:
                reaction = self.reactions[tipo]
                subject = reaction["subject"]
                message = subject + ": " + desc
                executeMethod = reaction["execute"]
                if executeMethod:
                    # Get lock to synchronize threads
                    logging.debug("Acquiring lock...")
                    self.threadLock.acquire()
                    
                    executeMethod(subject, message, param)
                    
                    # Free lock to release next thread
                    self.threadLock.release()
                    logging.debug("Lock released!")
            else:
                logging.warn("Evento sconosciuto: " + tipo + ": " + desc)
            
            return
    
    def inserimentoTotale(self, subject, message, param):
        self.alarmActive = INSERIMENTO_TOTALE

        for command in Config.getArray('pi_commands_on_arm'):
            AlarmManager.callPiServer(command)
        
        if param is not None and len(param) > 0:
            iParam = int(param)
            greets=Config.getArray('greetings_on_arm', '|')
            if len(greets) >= iParam:
                self.callTaskerTask("Pronuncia", greets[iParam-1])
                
        AlarmManager.sendTelegramMessage(subject)
        self.callTaskerTask("Disattiva_Aereo")

        time.sleep(1)
       
    def inserimentoParziale(self, subject, message, param):
        self.alarmActive = INSERIMENTO_PARZIALE
        
        AlarmManager.sendTelegramMessage(subject)
    
    def disinserimento(self, subject, message, param):
        if param is not None and len(param) > 0:
            iParam = int(param)
            greets=Config.getArray('greetings_on_disarm', '|')
            if len(greets) >= iParam:
                self.callTaskerTask("Pronuncia", greets[iParam-1])

        if self.alarmActive==INSERIMENTO_TOTALE: 
            for command in Config.getArray('pi_commands_on_disarm'):
                AlarmManager.callPiServer(command)
             
        self.alarmActive = DISINSERIMENTO    
    
        AlarmManager.sendTelegramMessage(subject)
        self.callTaskerTask("Attiva_Aereo")
        
    def inviaSmsEdEmail(self, subject, message, param):
        self.sendSms(message)
        self.sendCriticalTelegramMessage(subject + '; ' + message)
        self.sendEmail(subject, message)
        
    def inviaSmsSeInseritoEdEmail(self, subject, message, param):
        if self.alarmActive:
            self.sendSms(message)
        
        self.sendCriticalTelegramMessage(subject + '; ' + message)
        self.sendEmail(subject, message)        
    
    def inviaSmsSeEmailNonFunziona(self, subject, message, param):
        if self.alarmActive:
            self.sendSms(message)
            
        self.sendCriticalTelegramMessage(subject + '; ' + message)
        self.sendEmail(subject, message)
        
    def sendEmail(self, subject, msg, param=None):
        logging.info("Invio email: " + msg)
        AlarmManager.sendTelegramMessage(subject + '; ' + msg)
        
        try:
            msg = MIMEText(msg)

            mailFrom = Config.get('mail_sender')
            mailTo = Config.get('mail_recipients')
            msg['Subject'] = subject
            msg['From'] = mailFrom
            msg['To'] = mailTo
    
            server = smtplib.SMTP(host=Config.get('mail_host'), port=Config.getInt('mail_port'), timeout=6)
            server.starttls()
            server.login(Config.get('mail_user'),Config.get('mail_password'))
            server.sendmail(mailFrom, re.findall("[^<>]+@[^<>]+", mailTo), msg.as_string())
            server.quit()
            
            return True
        except Exception as e:
            logging.error("Errore durante l'invio dell'email: " + str(e))
            return False

    @staticmethod
    def sendTelegramMessage(msg):
        logging.info("Invio telegram: " + msg)
        requestUrl = Config.get('telegram_message_url') + urllib.parse.quote_plus(msg)
        res = requests.get(requestUrl)
        if res.status_code != 200:
            logging.error("Error executing request, status: " + str(res.status_code) + ", request: " + requestUrl)
    
    @staticmethod
    def sendCriticalTelegramMessage(msg):
        logging.info("Invio telegram: " + msg)
        requestUrl = Config.get('telegram_critical_message_url') + urllib.quote_plus(msg)
        res = requests.get(requestUrl)
        if res.status_code != 200:
            logging.error("Error executing request, status: " + str(res.status_code) + ", request: " + requestUrl)
               
    def sendSms(self, msg):
        logging.info("Invio sms: " + msg)
        if DISABLE_SMS:
            logging.info("sms disabled")
            return
        
        self.callTaskerTask("Invia_SMS", msg)
    
    def callTaskerTask(self, taskName, par1=None, par2=None, par3=None):
        command = "am broadcast -a pl.bossman.taskerproxy.ACTION_TASK --es task_name " + taskName
        
        if par1:
            command += ' --es p1 "%s"' % par1
        if par2:
            command += ' --es p2 "%s"' % par2
        if par3:
            command += ' --es p3 "%s"' % par3
             
        self.sendAdbCommand(command)
        
    def sendAdbCommand(self, command):
        logging.debug("Invio comando Android: " + command)
        if DISABLE_ANDROID_CMDS:
            logging.info("Android commands disabled")
            return

        #self.adb.start_server()
        #self.adb.shell_command(command)
    
    @staticmethod
    def callPiServer(requestString):
        #requestString = requestString + "?client=raspberry&time=" + str(int(time.time()*1000));
        #logging.debug("Chiamata al server PiHome con request: " + requestString)
        #conn = httplib.HTTPConnection(Config.get('pi_server_url'))
        ##conn.request("GET", "/" + AlarmManager.encrypt(requestString))
        #conn.request("GET", "/" + requestString)
        #r1 = conn.getresponse()
        #logging.debug("Risposta del server PiHome: %s - %s" % (r1.status, r1.reason))
        
        # DISABILITATO TUTTO DA QUANDO HO MESSO HOME ASSISTANT
        #requestUrl = Config.get('pi_server_url') + requestString
        #res = requests.get(requestUrl)
        #if res.status_code != 200:
        #    msg = "Error executing request, status: " + str(res.status_code) + ", request: " + requestUrl
        #    logging.error(msg)
        #    AlarmManager.sendTelegramMessage(msg)
        riga_inutile=0
        
    @staticmethod
    def encrypt(message):
        cipher = Blowfish.new(Config.get('encrypt_passphrase'), Blowfish.MODE_CBC, Config.get('encrypt_iv'))
        pad = 8-(len(message)%8)
        for x in range(pad):  # @UnusedVariable
            message+=" "
        encrypted = cipher.encrypt(message)
        return base64.urlsafe_b64encode(encrypted)

if __name__ == "__main__":
    logging.info('-------- AlarmManager TEST startup --------')
    Config.load('/etc/alarmReceiver.conf')

    alarmManager = AlarmManager("/opt/adb")
    s = '"SIA-DCS"0091L0#001234[#001234|Nri0CL0]_06:43:58,02-15-2015'
    alarmManager.manageAlarmMessage(s)
    #alarmManager.callTaskerTask("Pronuncia", "Ciao Roby, bentornato a casa!")
