#!/usr/bin/env python3

import sys,os,requests,json,base64
import optparse,logging,datetime
from metadata import *
try:
    import boto3
except ImportError:
    pass
try:
    import pickle
except ImportError:
    pass

# keywords plus date format which contain timestamp in returned json
# Timestamps need to be timezone aware, assume all is in UTC for now.
# Aether spec does not state anything about timezones......
standarddateformat  = '%Y-%m-%dT%H:%M:%S.%f%z'
datestrings = (('date','%Y-%m-%dT%H:%M:%S.%f'), ('security_event_date', '%Y-%m-%dT%H:%M:%S%z'))

# {event type:date}
class Cache(object) :
    def __init__(self, region,logger,starttime):
        self.logger    = logger
        self.starttime = starttime
        self.counter   = 0
        self.cache     =  None
        self.d         = {}
    def Get(self,key) :
        return(datetime.datetime.strptime(self.starttime, standarddateformat))
    def Write(self,key, value):
        self.d[key]=value
        self.counter+=1
        if self.counter % 10 == 0:
            self.Flush()
    def Close(self) :
        self.Flush()
    def Flush(self) :
        pass
    def Print(self,fp) :
        for k,v in self.d.items() :
            fp.write('{} -- {}\n'.format(k,v.strftime(standarddateformat)))

class CacheFile(Cache) :
    def __init__(self, region,logger,starttime):
        self.logger = logger
        self.starttime = starttime
        self.counter = 0
        self.file = region
        self.fp = None
        self.d={}
        try:
            self.fp = open(os.path.join(sys.path[0],self.file),'rb')
            # use dict as fast cache
            self.d = pickle.load(self.fp)
            self.fp.close()
        except Exception as e:
            self.logger.warning(str(e))
            self.logger.warning('Filecache could not be opened: {}'.format(self.file))
            self.d={}

    def Get(self,key) :
        try:
            return(self.d[key])
        except KeyError:
            # Not in cache, use earliest date
            return(datetime.datetime.strptime(self.starttime, standarddateformat))

    # write dict to cache
    def Flush(self) :
        try:
            self.fp = open(os.path.join(sys.path[0],self.file),'wb')
            pickle.dump(self.d,self.fp)
            self.fp.truncate()
            self.fp.flush()
            self.fp.close()
        except Exception as e:
            self.logger.warning(str(e))
            self.logger.warning('Error writing last_eventid to cache')

class CacheAWSParameter(Cache) :
    def __init__(self,region,logger,starttime):
        self.logger    = logger
        self.starttime = starttime
        self.counter   = 0
        self.cache     = boto3.client('ssm', region_name=region)
        self.d         = {}

    # Check last event tokens or timestamps per key in AWS cache
    # key is event type, value is date: panda-1:20211123:13:02:45
    def Get(self,key) :
        # First try local cache
        try:
            return(self.d[key])
        except KeyError:
            pass
        # Retrieve from remote cache
        cachekey = 'panda-{}'.format(key)
        try:
            cachevalue = self.cache.get_parameter(Name=cachekey)['Parameter']['Value']
            # We have a string back from the cache, should be timestamp
            # Try timestamp
            self.d[key] = datetime.datetime.strptime(cachevalue, standarddateformat)
            return(self.d[key])
        except Exception as e:
            # No cache value found
            self.logger.warning(str(e))
            self.logger.warning('Error: {} not found in cache! Starting from {}. \n'.format(cachekey,self.starttime))
            return(datetime.datetime.strptime(self.starttime, standarddateformat))

    # write dict to cache
    def Flush(self) :
        for k,v in self.d.items() :
            cachekey = 'panda-{}'.format(k)
            try:
                # try date format
                x = self.cache.put_parameter(Name=cachekey, Type='String', Overwrite=True,Value=v.strftime(standarddateformat))
            except Exception as e:
                self.logger.warning(str(e))
                self.logger.warning('Error writing last_eventid to cache')

# Returns authentication token
def GetAuthentication(url,token,key) :
    ClientToken = (token + ":" + key).encode()
    Base64ClientToken = base64.b64encode(ClientToken)
    data        = {'grant_type':'client_credentials','scope':'api-access'}
    response    = requests.post(url=url,headers={"Authorization": "Basic " + Base64ClientToken.decode(),
                                                 "accept":"application/json","Content-Type": "application/x-www-form-urlencoded"},data=data)
    tokens = json.loads(response.text)
    return(tokens['access_token'])

def RetrievePandaEvents(accountid,apikey,token,url,params):
    api_call_headers = {'Authorization': 'Bearer ' + token, 'Content-Type':'application/json', 'WatchGuard-API-Key':apikey, 'Accept':'application/json'}
    response = requests.get(url, headers=api_call_headers,params=params)
    return(response)

def Marshal(d) :
    # Try to convert strings to ints, which will make the auto mapping in sumo work
    l=[]
    for k,v in d.items():
        if isinstance(v, str) :
            if len(v) == 0:
                l.append(k)
            try:
                d[k]=int(v)
            except ValueError:
                pass
    # remove empty strings
    for k in l:
        del(d[k])

    # Do lookups
    for k,v in d.items() :
        try:
            lookup=lookups[k]
            try:
                d[k]=lookup[v]
            except KeyError:
                pass
        except KeyError:
            pass
    return(d)

# Class for writing events to destination, either Sumo or stdout.
# Overwrites possible for alternate destinations
class EventWriter(object) :
    def __init__(self, **kwargs):
        self.index = kwargs.get('index','knmi')
        self.marshal  = kwargs.get('mfunc',None)
        self.logger   = kwargs.get('logger',None)

    # Send a kv-formatted message to stdout
    # message is json dict
    def send(self, message):
        if self.marshal != None :
            data=self.marshal(message)
        else :
            data = message.encode('utf-8', 'replace')
        sys.stdout.buffer.write(data)

class EventWriterJSON(EventWriter) :
    def __init__(self, **kwargs):
        self.marshal  = kwargs.get('mfunc',None)
        self.logger   = kwargs.get('logger',None)
        self.url      = kwargs.get('url','')

    # message is dict
    def send(self, message):
        if self.marshal != None :
            data=self.marshal(message)
        else :
            data=message
        r = requests.post(url=self.url, data=json.dumps(data))
        r.raise_for_status()
        if self.logger != None:
            self.logger.info('EventWriterJSON returned status {}'.format(r.status_code))

class EventWriterJSONStdOut(EventWriter) :
    def __init__(self, **kwargs):
        self.marshal  = kwargs.get('mfunc',None)
        self.url      = kwargs.get('url','')
        self.logger   = kwargs.get('logger',None)

    # message is dict
    def send(self, message):
        if self.marshal != None :
            data=self.marshal(message)
        else :
            data=message
        sys.stdout.write(json.dumps(data))
        sys.stdout.write('\n')

def ExtractSecurityEvents(baseurl,auth,opts,logger,cache,eventwriter) :
    # Start extaction from panda portal
    totalcount=0
    for type,descr in EventTypes.items() :
        eventcount=0
        logger.info('Extracting {}:{}'.format(type,descr))
        command = 'api/{}/accounts/{}/securityevents/{}/export/{}'.format(opts.version,opts.accountid,type,opts.period)
        url='{}{}'.format(baseurl,command)
        #params={'top':'2','count':'true'}
        params={}
        r = RetrievePandaEvents(opts.accountid,opts.apikey,auth,url,params)
        if r.status_code != 200:
            # Output debug information
            logger.warning("\n Return Code: " + str(r.status_code) + " " + r.reason)
            logger.warning("Path: " + r.request.path_url)
            logger.warning("Headers: ")
            logger.warning(r.request.headers)
            continue

        result=r.json()
        if result==None :
            continue
        # Get most recent timestamp from cache, note that returned results are not sorted on date!
        last = cache.Get(type)
        dtMax=last
        for d in result['data'] :
            # Find timestamp in returned event
            dt=None
            for k,f in datestrings :
                try:
                    dt=datetime.datetime.strptime(d[k],f)
                    d['TIMESTAMP'] = dt.strftime(standarddateformat)
                    break
                except (KeyError, ValueError) as e:
                    pass
            # Make sure dt is timezone aware, for now assume UTC
            if dt != None and (dt.tzinfo == None or dt.tzinfo.utcoffset(dt) == None) :
                # Change from timezone unaware to aware, assume UTC
                dt=dt.replace(tzinfo=datetime.timezone.utc)
            if dt != None and dt > last:
                d['eventtype']=type
                d['eventdescription']=descr
                eventwriter.send(d)
                eventcount+=1
                if dt > dtMax :
                    dtMax=dt
        if dtMax > last:
            cache.Write(type,dtMax)
        if eventcount > 0:
            logger.info('Tried to submit {} events for log type {}'.format(eventcount,descr))
        totalcount+=eventcount
    #eventwriter.send({'eventssend':totalcount,'eventdescription':'pandacloudscript'})

def ExtractUnmanagedDevices(baseurl,auth,opts,logger,cache,eventwriter) :
    # Extract unmanaged devices, only once a day
    last = cache.Get('unmanaged')
    logger.info('Last timestamp for list of unmanaged devices: {}'.format(last.strftime(standarddateformat)))
    if last < (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days = 1)):
        logger.info('Extract unmanaged devices')
        eventcount=0
        command='api/{}/accounts/{}/unmanageddevices'.format(opts.version,opts.accountid)
        url='{}{}'.format(baseurl,command)
        r = RetrievePandaEvents(opts.accountid,opts.apikey,auth,url,{})
        if r.status_code != 200:
            # Output debug information
            logger.warning("\n Return Code: " + str(r.status_code) + " " + r.reason)
            logger.warning("Path: " + r.request.path_url)
            logger.warning("Headers: ")
            logger.warning(r.request.headers)
            return
        result=r.json()
        if result==None :
            return
        for d in result['data'] :
            d['TIMESTAMP'] = datetime.datetime.now(datetime.timezone.utc).strftime(standarddateformat)
            d['eventdescription']='unmanaged'
            eventwriter.send(d)
            eventcount+=1
        if eventcount > 0:
            logger.info('Tried to submit {} events for log type {}'.format(eventcount,'unmanaged'))
            cache.Write('unmanaged',datetime.datetime.now(datetime.timezone.utc))

# Main code
def main():
    # Hardcoded items for now
    version  ='v1'
    accountid='ACC-0000000'
    apikey='nokey'
    accesspassword = 'accesspassword'    
    accessid = 'access id'
    authurl = 'https://api.deu.cloud.watchguard.com/oauth/token'
    baseurl = 'https://api.deu.cloud.watchguard.com/rest/'

    # Parse command line options
    p = optparse.OptionParser("usage: %prog [options]")
    p.add_option("-b", "--baseurl", dest="baseurl", default=baseurl, \
                     help = 'Base URL for api requests, defaults to {}'.format(baseurl))
    p.add_option("-a", "--authurl", dest="authurl", default=authurl, \
                     help = 'url for authentication, defaults to: {}'.format(authurl))
    p.add_option("-i", "--accessid", dest="accessid", default=accessid, \
                 help='access id token for authentication requests, default={}'.format(accessid))
    p.add_option("-p", "--accesspassword", dest="accesspassword", default=accesspassword, \
                     help = 'password for accessid, defaults to: {}'.format(accesspassword))
    p.add_option("-k", "--apikey", dest="apikey", default=apikey, \
                 help='api key, default={}'.format(apikey))
    p.add_option("-q", "--endpoint", dest="endpoint", default='https://endpoint1.collection.eu.sumologic.com/receiver/v1/http/', \
                     help = 'SUMO endpoint, defaults to: {}'.format('https://endpoint1.collection.eu.sumologic.com/receiver/v1/http/'))
    p.add_option("-v", "--version", dest="version", default=version, \
                     help = 'api version in url, defaults to: {}'.format(version))
    p.add_option("-c", "--command", dest="command", default='securityevents', \
                     help = 'Aether api command, defaults to: {}'.format('securityevents'))
    p.add_option("-z", "--accountid", dest="accountid", default=accountid, \
                     help = 'Watchguard account ID, defaults to: {}'.format('accountid'))
    p.add_option("-d", "--destination", dest="destination", default='sumo', \
                     help = 'destination, either sumo,stdout. Defaults to: {}'.format('sumo'))
    p.add_option("-r", "--region", dest="region", default='us-east-2', \
                     help = 'AWS region, defaults to: {}'.format('us-east-2'))
    p.add_option("-e", "--period", dest="period", type='int', default=7, \
                     help = 'Period in days for collecting data, either 7 or 1, defaults to 7')
    p.add_option("-l", "--log", dest="loglevel", default='WARNING', \
                     help = 'loglevel field, defaults to WARNING, options are: CRITICAL, ERROR, WARNING, INFO, DEBUG, NOTSET')
    p.add_option("-n", "--nolambda",
                  action="store_true", dest="nolambda", default=False,
                  help="Not running as AWS Lambda function, use local filecache. Default = False")
    p.add_option("--authonly",
                  action="store_true", dest="authonly", default=False,
                  help="Only request and print authentication token, no commands issued. Default = False")
    opts,args = p.parse_args()

    # For AWS lambda: try to overwrite some options from environment
    for x in ('baseurl','authurl','accessid','accesspassword','apikey','region','version','accountid','command','period','endpoint','loglevel','destination') :
        try:
            exec('opts.{}=os.environ[\'{}\']'.format(x,x))
        except Exception as e:
            pass
    try:
        opts.period = int(opts.period)
    except:
        pass

    # Initiate Logging
    numeric_level = getattr(logging, opts.loglevel.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError('Invalid log level: %s' % opts.loglevel)

    formatter = logging.Formatter('%(asctime)s,Level=%(levelname)s,\
                                   LOGGING=%(message)s', '%m/%d/%Y %H:%M:%S')
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(formatter)
    logger        = logging.getLogger('PANDA')
    logger.addHandler(sh)
    logger.level=numeric_level

    # Create output channel
    if opts.destination=='stdout':
        eventwriter=EventWriterJSONStdOut(mfunc=Marshal, logger=logger)
        logger.info('Writing events to stdout')
    else:
        # Must be sumo
        eventwriter=EventWriterJSON(url=opts.endpoint,mfunc=Marshal, logger=logger)
        logger.info('Writing events to Sumo: {}'.format(opts.endpoint))

    auth=GetAuthentication(opts.authurl,opts.accessid,opts.accesspassword)
    if opts.authonly:
        sys.stdout.write('Provided authentication token:\n{}\n\n'.format(auth))
        sys.exit(0)

    # Open up the cache, which remembers per event type what the date/time of the most recent event was
    starttime = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days = opts.period)).strftime(standarddateformat)

    try:
        logger.info('Opening cache.....: {}'.format(opts.nolambda))
        if opts.nolambda:
            logger.info('File cache.....')
            cache = CacheFile('lastpandaevents',logger, starttime)
            logger.info('Succeeded opening local file cache')
        else:
            logger.info('AWS parameter  cache.....')
            cache = CacheAWSParameter(opts.region,logger, starttime)
            logger.info('Succeeded opening AWS cache')
    except Exception as e:
        logger.warning(str(e))
        logger.warning('Could not open cache')
        return

    baseurl='{}aether-endpoint-security/aether-mgmt/'.format(opts.baseurl)
    logger.info('Panda URL: {}'.format(baseurl))

    # Start extraction from panda portal
    ExtractSecurityEvents(baseurl,auth,opts,logger,cache,eventwriter)

    # Extract unmanaged devices, only once a day
    ExtractUnmanagedDevices(baseurl,auth,opts,logger,cache,eventwriter)

    cache.Close()

if __name__ == '__main__':
    main()

def lambda_handler(event, context):
    main()
    return {
        'statusCode': 200,
        'body': json.dumps('Sumo\'s Panda Lambda returns\n')
    }

