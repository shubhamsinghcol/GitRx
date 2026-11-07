"""Exactly one reusable HTTPS connection and one in-flight response."""
import http.client
from urllib.parse import urlsplit

class Response:
    def __init__(self,response,owner):self.response=response;self.owner=owner;self.code=response.status;self.headers=response.headers
    def __enter__(self):return self
    def read(self,n):return self.response.read(n)
    def __exit__(self,*args):
        self.response.close();self.owner.active=False
        if self.response.will_close:self.owner.close()

class SingleConnection:
    def __init__(self):self.connection=None;self.host=None;self.active=False;self.serial=0
    def close(self):
        if self.connection is not None:self.connection.close()
        self.connection=None;self.host=None;self.active=False
    def open(self,req,timeout=60):
        if self.active:raise RuntimeError('Concurrent HTTP response forbidden')
        u=urlsplit(req.full_url)
        if self.host!=u.netloc or self.connection is None:
            self.close();self.connection=http.client.HTTPSConnection(u.hostname,u.port or 443,timeout=timeout);self.host=u.netloc;self.serial+=1
        self.active=True
        try:
            self.connection.request('GET',u.path+('?' +u.query if u.query else ''),headers=dict(req.header_items()))
            return Response(self.connection.getresponse(),self)
        except BaseException:self.close();raise
