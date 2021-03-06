#!/usr/bin/env python
"""
A prototype of the example connector app we will publish.
"""


# Standard Library Imports
import argparse
from datetime import datetime
import json
import logging
import os
import time

# Third party
import requests

# Local
from panoptoindexconnector.connector_config import ConnectorConfig
from panoptoindexconnector.target_handler import TargetHandler


LOG = logging.getLogger(__name__)


###################################################################################################
#
# Methods
#
###################################################################################################


def get_ids_to_update(oauth_token, panopto_site_address, from_date, next_token):
    """
    Get the ids to update from Panopto
    """

    url = '{site}/Panopto/api/v1/searchIndexSync/updates?'.format(site=panopto_site_address)
    params = {
        'fromDate': from_date,
        'nextToken': next_token,
    }
    headers = {'Authorization': 'Bearer ' + oauth_token}

    response = requests.get(url=url, params=params, headers=headers)
    response.raise_for_status()

    LOG.debug('Received updates response %s', json.dumps(response.json(), indent=2))
    LOG.info('Received %i updates to process', len(response.json().get('Updates')))

    return response.json()


def get_last_update_time(implementation_name):
    """
    Read last update time
    """

    home = os.path.expanduser('~')
    file_name = os.path.join(home, '.panopto-connector.' + implementation_name)

    # Assume a default from before the site existed
    last_update_time = datetime(2008, 1, 1)

    # If the tracking file can be found and contains content, use that as the last update time
    if os.path.exists(file_name):
        with open(file_name, 'r') as file_handle:
            lines = file_handle.readlines()
            if lines:
                last_update_time_str = lines[-1].strip()
                last_update_time = datetime.fromisoformat(last_update_time_str)

    return last_update_time


def get_oauth_token(panopto_site_address, panopto_oauth_credentials):
    """
    Get an oauth token from Panopto
    """
    url = '{site}/Panopto/oauth2/connect/token'.format(site=panopto_site_address)

    data = {
        'client_id': panopto_oauth_credentials['client_id'],
        'client_secret': panopto_oauth_credentials['client_secret'],
        'grant_type': panopto_oauth_credentials['grant_type'],
        'scope': 'api',
    }
    # grant_type password uses username and password, others don't
    if panopto_oauth_credentials.get('username'):
        data['username'] = panopto_oauth_credentials['username']
    if panopto_oauth_credentials.get('password'):
        data['password'] = panopto_oauth_credentials['password']

    # sending get request and saving the response as response object
    response = requests.post(url=url, data=data)
    LOG.debug(response.content)
    response.raise_for_status()

    return response.json()['access_token']


def get_video_content(oauth_token, panopto_site_address, video_id):
    """
    Get the video content to update
    """

    url = '{site}/Panopto/api/v1/searchIndexSync/content?'.format(site=panopto_site_address)
    params = {'id': video_id}
    headers = {'Authorization': 'Bearer ' + oauth_token}

    response = requests.get(url=url, params=params, headers=headers)
    response.raise_for_status()

    LOG.debug('Received content response %s', json.dumps(response.json(), indent=2))

    return response.json()


def save_last_update_time(last_update_time, implementation_name):
    """
    Save the last update time to the state tracking file
    """

    home = os.path.expanduser('~')
    file_name = os.path.join(home, '.panopto-connector.' + implementation_name)

    with open(file_name, 'a') as file_handle:
        file_handle.write(last_update_time.isoformat() + '\n')


def sync_video_by_id(handler, oauth_token, config, video_id):
    """
    Sync video metadata from Panopto to target by ID
    """

    video_content_response = get_video_content(oauth_token, config.panopto_site_address, video_id)
    if video_content_response['Deleted']:
        handler.delete_from_target(video_content_response['Id'])
    else:
        target_content = handler.convert_to_target(video_content_response)
        handler.push_to_target(target_content, config)


###################################################################################################
#
# System layer
#
###################################################################################################


def run(config):
    """
    Run a sync given a config
    """

    assert isinstance(config, ConnectorConfig), 'config must be of type %s' % ConnectorConfig

    # Get time to update from
    last_update_time = get_last_update_time(config.target_implementation)

    while True:
        LOG.info('Beginning search index sync')

        start_time = datetime.utcnow()
        last_update_time, exception = sync(config, last_update_time)

        if exception:

            LOG.exception('Failed to sync the search index: current up to %s | %s', last_update_time, exception)
            remaining_time = config.polling_retry_minimum

        else:
            remaining_time = start_time + config.polling_frequency - datetime.utcnow()

        save_last_update_time(last_update_time, config.target_implementation)

        wait(remaining_time)


def sync(config, last_update_time):
    """
    Query for updates and run a sync up to the current point in time
    """
    LOG.info('Beginning incremental sync from %s to %s beginning at %s.',
             config.panopto_site_address, config.target_address, last_update_time)

    handler = TargetHandler(config)

    oauth_token = get_oauth_token(config.panopto_site_address, config.panopto_oauth_credentials)
    next_token = None
    exception = None
    start_time = datetime.utcnow()
    new_last_update_time = last_update_time

    try:
        for _ in range(1000):
            get_ids_response = get_ids_to_update(oauth_token, config.panopto_site_address, last_update_time, next_token)
            for update in get_ids_response['Updates']:
                video_id = update['VideoId']
                # Strip off the floats as datetime package only accepts exactly 6 digits of float,
                # and we don't need that level of precision
                update_time_str = update['UpdateTime'].split('.')[0]
                # Parse the last time the document was updated by panopto API format
                update_time = datetime.strptime(update_time_str, '%Y-%m-%dT%H:%M:%S')
                sync_video_by_id(handler, oauth_token, config, video_id)
                new_last_update_time = update_time
                # Sleep to avoid getting throttled by the API
                time.sleep(2)
            next_token = get_ids_response['NextToken']
            if next_token is None:
                LOG.info('Sync complete')
                break
        else:
            LOG.warning('Did not complete a sync in 1000 passes')
    except requests.exceptions.HTTPError as ex:
        LOG.exception('Received error response %s | %s', ex.response.status_code, ex.response.text)
        exception = ex
    except Exception as ex:  # pylint: disable=broad-except
        exception = ex

    if exception is None:
        new_last_update_time = start_time

    return new_last_update_time, exception


def wait(remaining_time):
    """
    Wait the remaining time
    """

    wait_seconds = remaining_time.total_seconds()
    if wait_seconds <= 0:
        LOG.warning('Received a negative wait time. Continuing now')
    else:
        LOG.info('Waiting %s until next sync', remaining_time)
        time.sleep(wait_seconds)


###################################################################################################
#
# Script handling
#
###################################################################################################


def main():
    """
    CLI entry point
    """

    args = parse_args()
    set_logger(args.logging_level)

    config = ConnectorConfig(args.configuration_file)
    LOG.info('Starting connector with configuration \n%s', config)

    run(config)


def parse_args():
    """
    Parse commandline arguments.
    """

    # Description
    parser = argparse.ArgumentParser(description='Connect a Panopto search index with an external index.')

    # Logging levels, local and third party
    parser.add_argument('--logging-level', choices=['warn', 'info', 'debug'], default='info')

    parser.add_argument('-c', '--configuration-file', required=True, help='Path to a config file')

    return parser.parse_args()


def set_logger(logging_level):
    """
    Set the logging level and format
    """

    # Add logging setup here
    log_format = '%(asctime)s %(levelname)-8s%(module)16s - %(message)s'
    log_date_format = '%Y-%m-%d %H:%M:%S'

    # Set logging level
    logging_level = logging.getLevelName(logging_level.upper())
    logging.basicConfig(format=log_format, level=logging_level, datefmt=log_date_format)


if __name__ == '__main__':
    main()
