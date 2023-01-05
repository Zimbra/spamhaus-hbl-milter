#!/usr/bin/python3
## To roll your own milter, create a class that extends Milter.  
#  This is a useless example to show basic features of Milter. 
#  See the pymilter project at https://pymilter.org based 
#  on Sendmail's milter API 
#  This code is open-source on the same terms as Python.

## Milter calls methods of your class at milter events.
## Return REJECT,TEMPFAIL,ACCEPT to short circuit processing for a message.
## You can also add/del recipients, replacebody, add/del headers, etc.

## See: https://github.com/sdgathman/pymilter

## apt-get install python3-dnspython

from __future__ import print_function
import Milter
try:
  from StringIO import StringIO as BytesIO
except:
  from io import BytesIO
import time
import email
from email import message_from_binary_file
from email import policy
import mimetypes
import os
import sys
from socket import AF_INET, AF_INET6
from Milter.utils import parse_addr
from email.utils import parseaddr
import re, hashlib, base64
import dns
import dns.resolver


if True:
  # for logging process - usually not needed
  from multiprocessing import Process as Thread, Queue
else:
  from threading import Thread
  from Queue import Queue

logq = None

class myMilter(Milter.Base):
  def spamhausNormalize(self, e):
    try:
      email = parseaddr(e)
      email = email[1]
      if(self.basicEmailValidate(email)):
        email = email.lower()
        emailparts = email.split('@');
        user = emailparts[0]
        domain = emailparts[1]
        if domain == "googlemail.com":
          domain = "gmail.com"
        head, sep, tail = user.partition('+')

        if domain == "gmail.com":
          head = head.replace('.', "")

        normalized = head + '@' + domain
        return normalized
    except Exception as e:
      return Milter.CONTINUE

  def basicEmailValidate(self, email):
    regex = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    if(re.fullmatch(regex, email)):
      return True
    else:
      return False

  def makeHash(self, a_string):
    try:
       hashed = hashlib.sha256(a_string.encode('utf-8')).digest()
       return_string = base64.b32encode(hashed).decode().rstrip("=")
       return return_string
    except Exception as e:
      return Milter.CONTINUE

  def queryHBL(self, email):
    try:
      hash_string = self.makeHash(self.spamhausNormalize(email))
      #if there is a result, it means the hash is listed in HBL
      self.log("DNS Lookup: " + hash_string + "._email.[hidden].hbl.dq.spamhaus.net")
      result = dns.resolver.resolve(hash_string+"._email."+self.DQSkey+".hbl.dq.spamhaus.net", 'A')
      self.log(hash_string + " is listed in HBL.")
      self.isDQSlisted = 'true'
      #return Milter.REJECT for hard reject
      return Milter.CONTINUE
    except Exception as e:
      #The query failed or the hash is not listed
      return Milter.CONTINUE

  def __init__(self):  # A new instance with each new connection.
    self.id = Milter.uniqueID()  # Integer incremented with each call.
    self.DQSkey = "PUT_DQS_KEY_HERE"
    self.isDQSlisted = 'false';

  # each connection runs in its own thread and has its own myMilter
  # instance.  Python code must be thread safe.  This is trivial if only stuff
  # in myMilter instances is referenced.
  @Milter.noreply
  def connect(self, IPname, family, hostaddr):
    # (self, 'ip068.subnet71.example.com', AF_INET, ('215.183.71.68', 4720) )
    # (self, 'ip6.mxout.example.com', AF_INET6,
    #	('3ffe:80e8:d8::1', 4720, 1, 0) )
    self.IP = hostaddr[0]
    self.port = hostaddr[1]
    if family == AF_INET6:
      self.flow = hostaddr[2]
      self.scope = hostaddr[3]
    else:
      self.flow = None
      self.scope = None
    self.IPname = IPname  # Name from a reverse IP lookup
    self.H = None
    self.fp = None
    self.receiver = self.getsymval('j')
    self.log("connect from %s at %s" % (IPname, hostaddr) )
    return Milter.CONTINUE


  ##  def envfrom(self,f,*str):
  def envfrom(self, mailfrom, *str):
    self.F = mailfrom
    self.R = []  # list of recipients
    self.fromparms = Milter.dictfromlist(str)	# ESMTP parms
    self.user = self.getsymval('{auth_authen}')	# authenticated user
    self.log("mail from:", mailfrom, *str)
    # NOTE: self.fp is only an *internal* copy of message data.  You
    # must use addheader, chgheader, replacebody to change the message
    # on the MTA.
    self.fp = BytesIO()
    self.canon_from = '@'.join(parse_addr(mailfrom))
    self.fp.write(b'From %s %s\n' % (self.canon_from.encode(),
        time.ctime().encode()))
    return Milter.CONTINUE


  ##  def envrcpt(self, to, *str):
  @Milter.noreply
  def envrcpt(self, to, *str):
    rcptinfo = to,Milter.dictfromlist(str)
    self.R.append(rcptinfo)
    return Milter.CONTINUE

  @Milter.noreply
  def header(self, name, hval):
    self.fp.write(b'%s: %s\n' % (name.encode(),hval.encode()))  # add header to buffer
    if name == 'From':
       self.fromHeader = '%s' % hval
       self.queryHBL(self.fromHeader)
    if name == 'Sender':
       self.senderHeader = '%s' % hval
       self.queryHBL(self.fromHeader)
    return Milter.CONTINUE

  @Milter.noreply
  def eoh(self):
    self.fp.write(b'\n')				# terminate headers
    return Milter.CONTINUE

  def eom(self):
    self.fp.seek(0)
    if 'true' in self.isDQSlisted:
      self.addheader('X-Spam-Flag','YES')
    return Milter.CONTINUE

  ## === Support Functions ===

  def log(self,*msg):
    t = (msg,self.id,time.time())
    if logq:
      logq.put(t)
    else:
      # logmsg(*t)
      pass

def logmsg(msg,id,ts):
    print("%s [%d]" % (time.strftime('%Y%b%d %H:%M:%S',time.localtime(ts)),id),
        end=None)
    # 2005Oct13 02:34:11 [1] msg1 msg2 msg3 ...
    for i in msg: print(i,end=None)
    print()
    sys.stdout.flush()

def background():
  while True:
    t = logq.get()
    if not t: break
    logmsg(*t)

## ===
    
def main():
  bt = Thread(target=background)
  bt.start()
  #socketname = os.getenv("HOME") + "/pythonsock"
  socketname = "inet:8802"
  timeout = 600
  # Register to have the Milter factory create instances of your class:
  Milter.factory = myMilter
  flags = Milter.CHGBODY + Milter.CHGHDRS + Milter.ADDHDRS
  flags += Milter.ADDRCPT
  flags += Milter.DELRCPT
  Milter.set_flags(flags)       # tell Sendmail which features we use
  print("%s milter startup" % time.strftime('%Y%b%d %H:%M:%S'))
  sys.stdout.flush()
  Milter.runmilter("pythonfilter",socketname,timeout)
  logq.put(None)
  bt.join()
  print("%s milter shutdown" % time.strftime('%Y%b%d %H:%M:%S'))

if __name__ == "__main__":
  # You probably do not need a logging process, but if you do, this
  # is one way to do it.
  logq = Queue(maxsize=4)
  main()

