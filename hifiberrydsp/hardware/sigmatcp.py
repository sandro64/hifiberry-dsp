'''
Copyright (c) 2018 Modul 9/HiFiBerry

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''

import socket
import time
import os
import logging
import hashlib
import tempfile
import shutil

from socketserver import BaseRequestHandler, TCPServer, ThreadingMixIn

import xmltodict
from hifiberrydsp.hardware import adau145x

COMMAND_READ = 0x0a
COMMAND_READRESPONSE = 0x0b
COMMAND_WRITE = 0x09
COMMAND_EEPROM_FILE = 0xf0
COMMAND_CHECKSUM = 0xf1
COMMAND_CHECKSUM_RESPONSE = 0xf2
COMMAND_EEPROM_CONTENT = 0xf3

HEADER_SIZE = 14

DEFAULT_PORT = 8086

MAX_READ_SIZE = 1024 * 2


class SigmaTCPException(IOError):

    def __init__(self, message):
        super(SigmaTCPException, self).__init__(message)


class SigmaTCP():

    def __init__(self, dsp, ip, port=DEFAULT_PORT, autoconnect=True):
        self.ip = ip
        self.port = port
        self.dsp = dsp
        self.autoconnect = autoconnect
        self.socket = None

    def connect(self):
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.ip, self.port))
        except IOError:
            self.socket = None
            raise SigmaTCPException(
                "Could not connect to {}:{}".format(self.ip, self.port))

    def disconnect(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    def read_memory(self, addr, length):
        if self.socket is None:
            if self.autoconnect:
                self.connect()
            else:
                raise SigmaTCPException("Not connected")

        packet = self.read_request(addr, length)
        self.socket.send(packet)
        data = self.socket.recv(HEADER_SIZE + length)
        # remove the header
        data = data[HEADER_SIZE:]
        return data

    def program_checksum(self):
        if self.socket is None:
            if self.autoconnect:
                self.connect()
            else:
                raise SigmaTCPException("Not connected")

        packet = self.generic_request(COMMAND_CHECKSUM)
        self.socket.send(packet)
        data = self.socket.recv(HEADER_SIZE + 16)
        # remove the header
        data = data[HEADER_SIZE:]
        return data

    def write_memory(self, addr, data):
        if self.socket is None:
            if self.autoconnect:
                self.connect()
            else:
                raise SigmaTCPException("Not connected")

        packet = self.write_request(addr, data)
        self.socket.send(packet)

    def write_eeprom(self, filename):
        if self.socket is None:
            if self.autoconnect:
                self.connect()
            else:
                raise SigmaTCPException("Not connected")

        if (os.path.exists(filename)):
            packet = self.write_eeprom_request(os.path.abspath(filename))
            self.socket.send(packet)
            result = int.from_bytes(self.socket.recv(1),
                                    byteorder='big',
                                    signed=False)
            if result == 1:
                return(True)
            else:
                return False
        else:
            raise IOError("{} does not exist".format(filename))

    def get_decimal_repr(self, value):
        data = self.dsp.decimal_repr(value)
        return SigmaTCP.int_data(data, self.dsp.DECIMAL_LEN)

    def write_decimal(self, addr, value):
        self.write_memory(addr, self.get_decimal_repr(value))

    def read_decimal(self, addr):
        data = self.read_memory(addr, self.dsp.DECIMAL_LEN)
        return self.dsp.decimal_val(self.data_int(data))

    def read_data(self, addr, length=None):
        if length == None:
            length = self.dsp.DECIMAL_LEN
        return self.read_memory(addr, length)

    def write_biquad(self, start_addr, bq):

        bqn = bq.normalized()
        bq_params = []
        bq_params.append(-bqn.a1)
        bq_params.append(-bqn.a2)
        bq_params.append(bqn.b0)
        bq_params.append(bqn.b1)
        bq_params.append(bqn.b2)

        reg = start_addr + 4
        for param in bq_params:
            self.write_decimal(reg, param)
            reg = reg - 1

        # reset a1/a2 to their original values
        bq_params[0] = -bq_params[0]
        bq_params[1] = -bq_params[1]

    def write_decibel(self, addr, db):
        amplification = pow(10, db / 20)
        self.write_decimal(addr, amplification)

    def read_request(self, addr, length):
        packet = bytearray(HEADER_SIZE)
        packet[0] = COMMAND_READ
        packet[4] = 14  # packet length
        packet[9] = length & 0xff
        packet[8] = (length >> 8) & 0xff
        packet[11] = addr & 0xff
        packet[10] = (addr >> 8) & 0xff

        return packet

    def write_request(self, addr, data):
        length = len(data)
        packet = bytearray(HEADER_SIZE)
        packet[0] = COMMAND_WRITE
        packet[11] = length & 0xff
        packet[10] = (length >> 8) & 0xff
        packet[13] = addr & 0xff
        packet[12] = (addr >> 8) & 0xff
        for d in data:
            packet.append(d)

        packet_length = len(packet)
        packet[6] = packet_length & 0xff
        packet[5] = (packet_length >> 8) & 0xff

        return packet

    def write_eeprom_request(self, filename):
        packet = bytearray(HEADER_SIZE)
        packet[0] = COMMAND_EEPROM_FILE
        packet[1] = len(filename)
        packet.extend(map(ord, filename))
        packet.extend([0])
        return packet

    def generic_request(self, request_type):
        packet = bytearray(HEADER_SIZE)
        packet[0] = request_type
        return packet

    def reset(self):
        (register, length) = self.dsp.reset_register()
        self.write_memory(register, SigmaTCP.int_data(0, length))
        time.sleep(0.5)
        self.write_memory(register, SigmaTCP.int_data(1, length))

    def hibernate(self, onoff):
        (register, length) = self.dsp.hibernate_register()
        if onoff:
            self.write_memory(register, SigmaTCP.int_data(1, length))
        else:
            self.write_memory(register, SigmaTCP.int_data(0, length))

    @staticmethod
    def int_data(intval, length=4):
        octets = bytearray()
        for i in range(length, 0, -1):
            octets.append((intval >> (i - 1) * 8) & 0xff)

        return octets

    def data_int(self, data):
        res = 0
        for d in data:
            res = res * 256
            res += d
        return res


class SigmaTCPHandler(BaseRequestHandler):

    spi = None

    def __init__(self, request, client_address, server):
        logging.debug("__init__")
        BaseRequestHandler.__init__(self, request, client_address, server)

        self.dspprogramfile = "/etc/dspprogram.xml"
        self.paramaterfile = "/etc/dspparameters.dat"

    def setup(self):
        import spidev

        logging.debug("setup")
        if SigmaTCPHandler.spi is None:
            SigmaTCPHandler.spi = spidev.SpiDev()
            SigmaTCPHandler.spi.open(0, 0)
            SigmaTCPHandler.spi.bits_per_word = 8
            SigmaTCPHandler.spi.max_speed_hz = 1000000
            SigmaTCPHandler.spi.mode = 0
            logging.debug("spi initialized %s", self.spi)

        self.dsp = adau145x.Adau145x()

        logging.debug("setup finished")

    def handle(self):
        logging.debug('handle')
        finished = False
        data = None
        read_more = False

        while not(finished):
            # Read dara
            try:
                buffer = None

                if data is None:
                    data = self.request.recv(65536)
                    if len(data) == 0:
                        finished = True
                        continue

                if read_more:
                    logging.debug("waiting for more data")
                    d2 = self.request.recv(65536)
                    data = data + d2
                    read_more = False

                # Not an expected header?
                if len(data) > 0 and len(data) < 14:
                    read_more = True
                    continue

                if data[0] == COMMAND_READ:
                    command_length = int.from_bytes(
                        data[1:5], byteorder='big')
                    if (command_length > 0) and (len(data) < command_length):
                        read_more = True
                        logging.debug(
                            "Expect %s bytes from header information (read), but have only %s", command_length, len(data))
                        continue

                    result = self.handle_read(data)

                elif data[0] == COMMAND_WRITE:
                    command_length = int.from_bytes(
                        data[3:7], byteorder='big')

                    logging.debug("Len (data, header info): %s %s",
                                  len(data), command_length)

                    if command_length < len(data):
                        buffer = data[command_length:]
                        data = data[0:command_length]

                    if (command_length > 0) and (len(data) < command_length):
                        read_more = True
                        logging.debug(
                            "Expect %s bytes from header information (write), but have only %s", command_length, len(data))
                        continue

                    self.handle_write(data)
                    result = None

                elif data[0] == COMMAND_EEPROM_FILE:
                    filename_length = data[1]
                    filename = "".join(map(chr, data[14:14 + filename_length]))
                    result = self.write_eeprom_file(filename)

                elif data[0] == COMMAND_EEPROM_FILE:
                    filename_length = data[1]
                    filename = "".join(map(chr, data[14:14 + filename_length]))
                    result = self.write_eeprom_file(filename)

                elif data[0] == COMMAND_CHECKSUM:
                    result = self._response_packet(
                        COMMAND_CHECKSUM_RESPONSE, 0, 16) + self.program_checksum()

                elif data[0] == COMMAND_EEPROM_CONTENT:
                    command_length = int.from_bytes(
                        data[3:7], byteorder='big')

                    logging.debug("Len (data, header info): %s %s",
                                  len(data), command_length)

                    if command_length < len(data):
                        buffer = data[command_length:]
                        data = data[0:command_length]

                    if (command_length > 0) and (len(data) < command_length):
                        read_more = True
                        logging.debug(
                            "Expect %s bytes from header information (write), but have only %s", command_length, len(data))
                        continue

                    result = self.write_eeprom_content(data[14:command_length])

                if (result is not None) and (len(result) > 0):
                    logging.debug(
                        "Sending %s bytes answer to client", len(result))
                    self.request.send(result)

                # Still got data that hasn't been processed?
                if buffer is not None:
                    data = buffer
                else:
                    data = None

            except ConnectionResetError:
                finished = True
            except BrokenPipeError:
                finished = True

    def handle_read(self, data):
        addr = int.from_bytes(data[10:12], byteorder='big')
        length = int.from_bytes(data[6:10], byteorder='big')

        a0 = data[11]
        a1 = data[10]

        logging.debug("read {} bytes from {}".format(length, addr))

        return self.spi_read(addr, length)

    def spi_read(self, addr, length):

        spi_request = []
        a0 = addr & 0xff
        a1 = (addr >> 8) & 0xff

        spi_request.append(1)
        spi_request.append(a1)
        spi_request.append(a0)

        for i in range(0, length):
            spi_request.append(0)

        spi_response = SigmaTCPHandler.spi.xfer(spi_request)  # SPI read
        logging.debug("spi read %s bytes from %s", len(spi_request), addr)

        res = self._response_packet(COMMAND_READRESPONSE,
                                    addr,
                                    len(spi_response[3:]))

        for b in spi_response[3:]:
            res.append(b)

        return res

    def handle_write(self, data):
        addr = int.from_bytes(data[12:14], byteorder='big')
        length = int.from_bytes(data[8:12], byteorder='big')
        if (length == 0):
            # Client might not implement length correctly and leave
            # it empty
            length = length(data) - 14

        safeload = data[1]  # TODO: use this

        logging.debug("writing {} bytes to {}".format(length, addr))
        return self.write_data(addr, data[14:])

    def write_eeprom_content(self, data):
        tempfile = tempfile.NamedTemporaryFile(mode='w+b',
                                               delete=False)
        try:
            tempfile.write(data)
            self.write_eeprom_file(tempfile.name)
            shutil.copy(tempfile.name, self.dspprogramfile)
            result = b'\01'
        except IOError:
            result = b'\00'

        try:
            tempfile.close()
            os.remove(tempfile.name)
        except:
            pass

        return result

    def write_eeprom_file(self, filename):

        with open(filename) as fd:
            doc = xmltodict.parse(fd.read())

        for action in doc["ROM"]["page"]["action"]:
            instr = action["@instr"]

            if instr == "writeXbytes":
                addr = int(action["@addr"])
                paramname = action["@ParamName"]
                data = []
                for d in action["#text"].split(" "):
                    value = int(d, 16)
                    data.append(value)

                self.write_data(addr, data)

                # Sleep after erase operations
                if ("g_Erase" in paramname):
                    time.sleep(10)

            if instr == "delay":
                time.sleep(1)

        return b'\01'

    def write_data(self, addr, data):

        a0 = addr & 0xff
        a1 = (addr >> 8) & 0xff

        spi_request = []
        spi_request.append(0)
        spi_request.append(a1)
        spi_request.append(a0)
        for d in data:
            spi_request.append(d)

        if len(spi_request) < 4096:
            SigmaTCPHandler.spi.xfer(spi_request)
            logging.debug("spi write %s bytes",  len(spi_request) - 3)
        else:
            finished = False
            while not finished:
                if len(spi_request) < 4096:
                    SigmaTCPHandler.spi.xfer(spi_request)
                    logging.debug("spi write %s bytes", len(spi_request) - 3)
                    finished = True
                else:
                    short_request = spi_request[:4003]
                    SigmaTCPHandler.spi.xfer(short_request)
                    logging.debug("spi write %s bytes", len(short_request) - 3)

                    # skip forward 1000 cells
                    addr = addr + 1000  # each memory cell is 4 bytes long
                    a0 = addr & 0xff
                    a1 = (addr >> 8) & 0xff
                    new_request = []
                    new_request.append(0)
                    new_request.append(a1)
                    new_request.append(a0)
                    new_request.extend(spi_request[4003:])

                    spi_request = new_request

        return data

    def program_checksum(self):
        '''
        Calculate a checksum of the program memory of the DSP
        '''
        addr = self.dsp.program_addr
        block_size = 2048

        datalen = 0
        m = hashlib.md5()

        logging.debug("reading %s bytes program code",
                      self.dsp.program_length * self.dsp.word_length)

        # Must kill the core to read program memory :(
        self._kill_dsp()

        while datalen < self.dsp.program_length * self.dsp.word_length:
            logging.debug("reading program code block from addr %s", addr)
            data = self.spi_read(addr, block_size)
            print(data)
            m.update(data)
            addr = addr + int(block_size / self.dsp.word_length)
            datalen += block_size

        # Restart the core
        self._start_dsp()

        logging.debug("digest: %s", m.digest())
        return m.digest()

    def finish(self):
        logging.debug('finish')

    def _list_str(self, int_list):
        formatted_list = [str(item) for item in int_list]
        return "[" + ','.join(formatted_list) + "]"

    def _response_packet(self, command, addr, data_length):
        packet = bytearray(HEADER_SIZE)
        packet[0] = command
        packet[4] = 14  # header length
        packet[5] = 1  # chip address

        packet[9] = data_length & 0xff
        packet[8] = (data_length >> 8) & 0xff

        packet[11] = addr & 0xff
        packet[10] = (addr >> 8) & 0xff

        return packet

    def _kill_dsp(self):
        logging.debug("killing DSP core")
        (register, _length) = self.dsp.hibernate_register()
        self.write_data(register, [0, 1])
        time.sleep(0.001)
        (register, _length) = self.dsp.killcore_register()
        self.write_data(register, [0, 0])
        self.write_data(register, [0, 1])

    def _start_dsp(self):
        logging.debug("starting DSP core")
        (register, _length) = self.dsp.startcore_register()
        self.write_data(register, [0, 0])
        self.write_data(register, [0, 1])


class SigmaTCPServer(ThreadingMixIn, TCPServer):

    def __init__(self,
                 server_address=("0.0.0.0", DEFAULT_PORT),
                 RequestHandlerClass=SigmaTCPHandler):
        self.allow_reuse_address = True

        TCPServer.__init__(self, server_address, RequestHandlerClass)
