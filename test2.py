#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  This Program aims to combine Lighshow Pi and BilblioPixel to create a  led strip
#That responds to music imput through the usb port


#imports from the New lighshowpi sync lights 

import argparse
import csv
import fcntl
import gzip
import json
import logging
import os
import random
import subprocess
import sys
import wave

import alsaaudio as aa
import fft
import configuration_manager as cm
import decoder
import hardware_controller as hc
import numpy as np

from preshow import Preshow

##imports from scott driscolls hack of the ledstrip_lighshow Pi (duplicates removed)


from struct import unpack
from time import sleep
import time


#imports to make the lights work
from bibliopixel.animation import *
from bibliopixel.drivers.LPD8806 import *
from bibliopixel.led import *
from bibliopixel.colors import *
#from bibliopixel.animation import *

#how many lights (32 per strip)
led_array = [0 for i in range(31)]
num_lights = 32
#set instances
led_driver_LP = DriverLPD8806(num = num_lights, c_order= ChannelOrder.GRB, SPISpeed = 16) #SPI speed still isn't fully understood but this value seems to work
led = LEDStrip(led_driver_LP)
led.all_off()
#set led brightness (0-255)
led.fillRGB(r=50,g=0,b=50,start=0, end = num_lights)
led.update()
time.sleep(2.0)
led.all_off()
led.update()
#led.masterBrightness(100)
#led.update()

#This writes out light info to the LED strip 
#The colors are simply fading through all the colors######################################
c = 0.0
columns = [1.0,1.0,1.0,1.0,1.0]
decay = .9
# this writes out light and color information to a continuous RGB LED
# strip that's been wrapped around into 5 columns.
# numbers comes in at 9-15 ish
def display_ledStrip(col=0,height=0.0,color='Red'):
    global c
    global columns
    color = wheel_color(int(c))
    c = c + .1
    if c > 384:
        c = 0.0
    height = height - 9.0
    height = height / 5
    if height < .05:
        height = .05
    elif height > 1.0:
        height = 1.0
        
    if height < columns[col]:
        columns[col] = columns[col] * decay
        height = columns[col]
    else:
        columns[col] = height
    if col == 0:            
        led.fill(color,0,int(round(height*25)))
    elif col == 1:
        led.fill(color,56 - int(round(height*25)),56)
    elif col == 2:
        led.fill(color,62,62+int(round(height*25)))
    elif col == 3:
        led.fill(color,118- int(round(height*25)),118)
    elif col == 4:
        led.fill(color,123,123+int(round(height*25)))   

###########################################################################

# Configurations - TODO(todd): Move more of this into configuration manager
_CONFIG = cm.CONFIG
_MODE = cm.lightshow()['mode']
_MIN_FREQUENCY = _CONFIG.getfloat('audio_processing', 'min_frequency')
_MAX_FREQUENCY = _CONFIG.getfloat('audio_processing', 'max_frequency')
_RANDOMIZE_PLAYLIST = _CONFIG.getboolean('lightshow', 'randomize_playlist')
try:
    _CUSTOM_CHANNEL_MAPPING = [int(channel) for channel in
                               _CONFIG.get('audio_processing', 'custom_channel_mapping').split(',')]
except:
    _CUSTOM_CHANNEL_MAPPING = 0 ###############THIS COULD BE USEFUL FOR CUSTOME LIGHTS!!!!!!" "
try:
    _CUSTOM_CHANNEL_FREQUENCIES = [int(channel) for channel in
                                   _CONFIG.get('audio_processing',
                                               'custom_channel_frequencies').split(',')]
except:
    _CUSTOM_CHANNEL_FREQUENCIES = 0
try:
    _PLAYLIST_PATH = cm.lightshow()['playlist_path'].replace('$SYNCHRONIZED_LIGHTS_HOME', cm.HOME_DIR)
except: 
    _PLAYLIST_PATH = "/home/pi/music/.playlist"
try:
    _usefm=_CONFIG.get('audio_processing','fm');
    frequency =_CONFIG.get('audio_processing','frequency');
    play_stereo = True
    music_pipe_r,music_pipe_w = os.pipe()   
except:
    _usefm='false'
CHUNK_SIZE = 2048  # Use a multiple of 8 (move this to config)



def calculate_channel_frequency(min_frequency, max_frequency, custom_channel_mapping,
                                custom_channel_frequencies):
    '''Calculate frequency values for each channel, taking into account custom settings.'''

    # How many channels do we need to calculate the frequency for
    if custom_channel_mapping != 0 and len(custom_channel_mapping) == hc.GPIOLEN:
        logging.debug("Custom Channel Mapping is being used: %s", str(custom_channel_mapping))
        channel_length = max(custom_channel_mapping)
    else:
        logging.debug("Normal Channel Mapping is being used.")
        channel_length = hc.GPIOLEN

    logging.debug("Calculating frequencies for %d channels.", channel_length)
    octaves = (np.log(max_frequency / min_frequency)) / np.log(2)
    logging.debug("octaves in selected frequency range ... %s", octaves)
    octaves_per_channel = octaves / channel_length
    frequency_limits = []
    frequency_store = []

    frequency_limits.append(min_frequency)
    if custom_channel_frequencies != 0 and (len(custom_channel_frequencies) >= channel_length + 1):
        logging.debug("Custom channel frequencies are being used")
        frequency_limits = custom_channel_frequencies
    else:
        logging.debug("Custom channel frequencies are not being used")
        for i in range(1, hc.GPIOLEN + 1):
            frequency_limits.append(frequency_limits[-1]
                                    * 10 ** (3 / (10 * (1 / octaves_per_channel))))##########################frequency_limits[-1]*2**octaves_per_channel)### Old one
    for i in range(0, channel_length):
        frequency_store.append((frequency_limits[i], frequency_limits[i + 1]))
        logging.debug("channel %d is %6.2f to %6.2f ", i, frequency_limits[i],
                      frequency_limits[i + 1])

    # we have the frequencies now lets map them if custom mapping is defined
    if custom_channel_mapping != 0 and len(custom_channel_mapping) == hc.GPIOLEN:
        frequency_map = []
        for i in range(0, hc.GPIOLEN):
            mapped_channel = custom_channel_mapping[i] - 1
            mapped_frequency_set = frequency_store[mapped_channel]
            mapped_frequency_set_low = mapped_frequency_set[0]
            mapped_frequency_set_high = mapped_frequency_set[1]
            logging.debug("mapped channel: " + str(mapped_channel) + " will hold LOW: "
                          + str(mapped_frequency_set_low) + " HIGH: "
                          + str(mapped_frequency_set_high))
            frequency_map.append(mapped_frequency_set)
        return frequency_map
    else:
        return frequency_store


### NO NEED FOR PIFF OF FFT CACULATIONS AS THEY ARE IN THE OTHER FILE

def update_lights(matrix, mean, std): #This should work with led.....
    '''Update the state of all the lights based upon the current frequency response matrix'''
    #blank out strip
    led.fillRGB(r=0,g=0,b=0,start=0, end=num_lights)

    for i in range(0, hc.GPIOLEN):
        # Calculate output pwm, where off is at some portion of the std below
        # the mean and full on is at some portion of the std above the mean.
        if hc.is_pin_pwm(i):
        display_ledStrip(intensity = i)

        brightness = matrix[i] - mean[i] + 0.5 * std[i]
        brightness = brightness / (1.25 * std[i])
        if brightness > 1.0:
            brightness = 1.0
        if brightness < 0:
            brightness = 0
        if not hc.is_pin_pwm(i):
            # If pin is on / off mode we'll turn on at 1/2 brightness
            if (brightness > 0.5):
                hc.turn_on_light(i, True)
            else:
                hc.turn_off_light(i, True)
        else:
            hc.turn_on_light(i, True, brightness)

    ## added by darce
    #send out data to RGB LED strip
    led.update()
    
    

def audio_in():  ## Changed to main so always audio in mode
    '''Control the lightshow from audio coming in from a USB audio card'''
    sample_rate = cm.lightshow()['audio_in_sample_rate']
    input_channels = cm.lightshow()['audio_in_channels']

    # Open the input stream from default input device
    stream = aa.PCM(aa.PCM_CAPTURE, aa.PCM_NORMAL, cm.lightshow()['audio_in_card'])
    stream.setchannels(input_channels)
    stream.setformat(aa.PCM_FORMAT_S16_LE) # Expose in config if needed
    stream.setrate(sample_rate)
    stream.setperiodsize(CHUNK_SIZE)
         
    logging.debug("Running in audio-in mode - will run until Ctrl+C is pressed")
    print "Running in audio-in mode, use Ctrl+C to stop"
    try:
        hc.initialize()
        frequency_limits = calculate_channel_frequency(_MIN_FREQUENCY,
                                                       _MAX_FREQUENCY,
                                                       _CUSTOM_CHANNEL_MAPPING,
                                                       _CUSTOM_CHANNEL_FREQUENCIES)

        # Start with these as our initial guesses - will calculate a rolling mean / std 
        # as we get input data.
        mean = [12.0 for _ in range(hc.GPIOLEN)]
        std = [0.5 for _ in range(hc.GPIOLEN)]
        recent_samples = np.empty((250, hc.GPIOLEN))
        num_samples = 0
    
        # Listen on the audio input device until CTRL-C is pressed
        while True:            
            l, data = stream.read()
            
            if l:
                try:
                    matrix = fft.calculate_levels(data, CHUNK_SIZE, sample_rate, frequency_limits, input_channels)
                    if not np.isfinite(np.sum(matrix)):
                        # Bad data --- skip it
                        continue
                except ValueError as e:
                    # TODO(todd): This is most likely occuring due to extra time in calculating
                    # mean/std every 250 samples which causes more to be read than expected the
                    # next time around.  Would be good to update mean/std in separate thread to
                    # avoid this --- but for now, skip it when we run into this error is good 
                    # enough ;)
                    logging.debug("skipping update: " + str(e))
                    continue

                update_lights(matrix, mean, std)
                ######### ADDDED BY DARCE######



                # Keep track of the last N samples to compute a running std / mean
                #
                # TODO(todd): Look into using this algorithm to compute this on a per sample basis:
                # http://www.johndcook.com/blog/standard_deviation/                
                if num_samples >= 250:
                    no_connection_ct = 0
                    for i in range(0, hc.GPIOLEN):
                        mean[i] = np.mean([item for item in recent_samples[:, i] if item > 0])
                        std[i] = np.std([item for item in recent_samples[:, i] if item > 0])
                        
                        # Count how many channels are below 10, if more than 1/2, assume noise (no connection)
                        if mean[i] < 10.0:
                            no_connection_ct += 1
                            
                    # If more than 1/2 of the channels appear to be not connected, turn all off
                    if no_connection_ct > hc.GPIOLEN / 2:
                        logging.debug("no input detected, turning all lights off")
                        mean = [20 for _ in range(hc.GPIOLEN)]
                    else:
                        logging.debug("std: " + str(std) + ", mean: " + str(mean))
                    num_samples = 0
                else:
                    for i in range(0, hc.GPIOLEN):
                        recent_samples[num_samples][i] = matrix[i]
                    num_samples += 1
 
    except KeyboardInterrupt:
        pass
    finally:
        print "\nStopping"
        hc.clean_up()

