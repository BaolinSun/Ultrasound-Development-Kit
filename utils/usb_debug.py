import usb.core
import usb.util
import multiprocessing as mp
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
from scipy.io import savemat


def byte_to_double(data):
    if len(data)%2 !=0:
        raise ValueError("Data length must be even")
    return np.frombuffer(data, dtype='<u2')


# ============================================================

def usb_reader(data_queue, stop_flag):
    """
    USB数据采集进程
    """
    dev = usb.core.find(idVendor=0x0424, idProduct=0x4940)
    dev.set_configuration()
    cfg = dev.get_active_configuration()
    intf = cfg[(0,0)]
    ep = usb.util.find_descriptor(
        intf, 
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
    )
    print(ep)

    read_length = 1024 * 1000
    ready_data = [0xef, 0x01, 0x10, 0x00]

    

    line_header1 = 0x1400
    line_header2 = 0x1000
    rf_depth = 4096

    rfdata = np.zeros((64, rf_depth), dtype=np.int32)

    line_set = set()
    while True:
        recv_data = []
        recv_flag = True

        ready_data[1] = 0x01
        dev.write(0x1, ready_data, timeout=1000)

        while recv_flag:
            recv_flag = False
            data = dev.read(0x81, read_length, timeout=5000)


        break

    
    usb.util.dispose_resources(dev)
    print("[USB] Stop.")





if __name__ == "__main__":
   usb_reader(None, None)
