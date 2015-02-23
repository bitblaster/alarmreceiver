#!/usr/bin/python
# -*- coding: utf-8 -*- 

import logging
import threading
import re
import time
import smtplib
import httplib
import base64
from Crypto.Cipher import Blowfish
from email.mime.text import MIMEText
from ADB import ADB

DISABLE_SMS=False

DISINSERIMENTO = 0
INSERIMENTO_TOTALE = 1
INSERIMENTO_PARZIALE = 2

# Config section
config={}
config['pi_server_url']       = "192.168.0.150:8444"
config['encrypt_iv']          = "12345678"
config['encrypt_passphrase']  = "1234567890abcdef"

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
        self.adb=ADB(adbPath)
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
            "AT" : {"subject": "Mancanza alimentazione", "execute": self.sendEmail},
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
            "AR" : {"subject": "Ripristino alimentazione", "execute": self.sendEmail},
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
                    executeMethod(subject, message, param)
            else:
                logging.warn("Evento sconosciuto: " + tipo + ": " + desc)
            
            return
    
    def inserimentoTotale(self, subject, message, param):
        self.alarmActive = INSERIMENTO_TOTALE

        if param is not None and len(param) > 0:
            iParam = int(param)
            if iParam == 1:
                self.callTaskerTask("Pronuncia", "Ciao Roby, a presto!")
            elif iParam == 2:
                self.callTaskerTask("Pronuncia", "Ciao Cate, a presto!")

        AlarmManager.callPiServer("allOff/group:1")
                
        self.sendEmail(subject, message)
        self.callTaskerTask("Abilita_Cell")
       
    def inserimentoParziale(self, subject, message, param):
        self.alarmActive = INSERIMENTO_PARZIALE
        
        self.sendEmail(subject, message)
    
    def disinserimento(self, subject, message, param):
        if param is not None and len(param) > 0:
            iParam = int(param)
            if iParam == 1:
                self.callTaskerTask("Pronuncia", "Ciao Roby, bentornato a casa!")
            elif iParam == 2:
                self.callTaskerTask("Pronuncia", "Ciao Cate, bentornata a casa!")

        if self.alarmActive==INSERIMENTO_TOTALE: 
            AlarmManager.callPiServer("switchDeviceFuzzy/enable Lampada Soggiorno")
            
        self.alarmActive = DISINSERIMENTO    
    
        self.sendEmail(subject, message)
        self.callTaskerTask("Disabilita_Cell")
        
    def inviaSmsEdEmail(self, subject, message, param):
        self.sendSms(message)
        self.sendEmail(subject, message)
        
    def inviaSmsSeInseritoEdEmail(self, subject, message, param):
        if self.alarmActive:
            self.sendSms(message)
            
        self.sendEmail(subject, message)        
    
    def inviaSmsSeEmailNonFunziona(self, subject, message, param):
        if self.alarmActive:
            self.sendSms(message)
            
        self.sendEmail(subject, message)
        
    def sendEmail(self, subject, msg, param=None):
        # Get lock to synchronize threads
        logging.debug("Acquiring lock...")
        self.threadLock.acquire()
        
        logging.info("Invio email: " + msg)
        try:
            msg = MIMEText(msg)

            mailFrom = "Antifurto Casa <videomozzi@gmail.com>"
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
            self.threadLock.release()
            logging.debug("Lock released!")
        
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
        # Get lock to synchronize threads
        logging.debug("Acquiring lock...")
        self.threadLock.acquire()
        
        self.adb.start_server()
        self.adb.shell_command(command)
        
        # Free lock to release next thread
        self.threadLock.release()
        logging.debug("Lock released!")
    
    @staticmethod
    def callPiServer(requestString):
        requestString = requestString + "?client=raspberry&time=" + str(int(time.time()));
        logging.debug("Chiamata al server PiHome con request: " + requestString)
        conn = httplib.HTTPConnection(config['pi_server_url'])
        conn.request("GET", "/" + AlarmManager.encrypt(requestString))
        r1 = conn.getresponse()
        logging.debug("Risposta del server PiHome: %s - %s" % (r1.status, r1.reason))
        
    @staticmethod
    def encrypt(message):
        cipher = Blowfish.new(config['encrypt_passphrase'], Blowfish.MODE_CBC, config['encrypt_iv'])
        pad = 8-(len(message)%8)
        for x in range(pad):
            message+=" "
        encrypted = cipher.encrypt(message)
        return base64.urlsafe_b64encode(encrypted)

if __name__ == "__main__":
    alarmManager = AlarmManager("/opt/adb")
    s = '"SIA-DCS"0091L0#001234[#001234|Nri0CL0]_06:43:58,02-15-2015'
    alarmManager.manageAlarmMessage(s)
