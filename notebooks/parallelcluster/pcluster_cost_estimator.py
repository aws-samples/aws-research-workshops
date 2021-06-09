#!/usr/bin/python
#
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this
# software and associated documentation files (the "Software"), to deal in the Software
# without restriction, including without limitation the rights to use, copy, modify,
# merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
# PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
# The sample code; software libraries; command line tools; proofs of concept; templates; or other related technology (including any of the 
# foregoing that are provided by our personnel) is provided to you as AWS Content under the AWS Customer Agreement, or the relevant 
# written agreement between you and AWS (whichever applies). You should not use this AWS Content in your production accounts, or on 
# production or other critical data. You are responsible for testing, securing, and optimizing the AWS Content, such as sample code, as 
# appropriate for production grade use based on your specific quality control practices and standards. Deploying AWS Content may incur AWS 
# charges for creating or using AWS chargeable resources, such as running Amazon EC2 instances or using Amazon S3 storage.


import pandas as pd
import boto3
import time
from botocore.exceptions import ClientError


class PClusterCostEstimator:
    def __init__(self, cur_db_name, cur_table_name, query_bucket_name, query_path_name):
        self.cur_db_name=cur_db_name
        self.cur_table_name=cur_table_name
        self.query_bucket_name=query_bucket_name
        self.query_path_name=query_path_name
        self.athena_client= boto3.client('athena')
        self.s3_client = boto3.client('s3')
        
    def retrieve_cur_df (self, response, is_download=False, download_file_name=None):
        exec_id = response['QueryExecutionId']

        while True:
            resp = self.athena_client.get_query_execution(
                QueryExecutionId= exec_id
            )
            if resp['QueryExecution']['Status']['State'] == 'SUCCEEDED':
                print("Query completed")
                result = resp['QueryExecution']['ResultConfiguration']['OutputLocation']
                cur_df = pd.read_csv(result)
                if is_download:
                    file_name = result.split('/')[-1]
                    print(self.query_bucket_name, f'{self.query_path_name}/{file_name}')
                    s3_resp = self.s3_client.download_file(self.query_bucket_name, f'{self.query_path_name}/{file_name}', download_file_name)
                return cur_df
            elif resp['QueryExecution']['Status']['State'] == 'FAILED':
                print("Failed", resp['QueryExecution']['Status']['StateChangeReason'])
                break    
            else: 
                print("Query not completed yet",resp['QueryExecution']['Status']['State']  )
                time.sleep(5)

    def submit_query (self, sql_str):
        response = self.athena_client.start_query_execution(
            QueryString=sql_str,
            QueryExecutionContext={
                'Database': self.cur_db_name
            },
            ResultConfiguration={
                'OutputLocation': f's3://{self.query_bucket_name}/{self.query_path_name}/'
            }
        )
        return response

    def cluster_monthly_cost(self, cluster_name, year):
        sql_str = """SELECT bill_payer_account_id, month, sum(line_item_blended_cost) as monthly_cost FROM \"{}\".\"{}\" where year = '{}' 
        and resource_tags_user_cluster_name = '{}'
        and line_item_blended_cost > 0.001 group by month, bill_payer_account_id;""".format(self.cur_db_name, self.cur_table_name, year, cluster_name)
        
        response = self.submit_query(sql_str)

        return self.retrieve_cur_df(response, True, "cluster_monthly_{}_{}.csv".format(cluster_name, year))

    def cluster_daily_per_month(self, cluster_name, cur_year, cur_month):
        sql_str = """SELECT line_item_usage_start_date, sum(line_item_blended_cost) as cost  
            FROM \"{}\".\"{}\" where year = '{}' and month ='{}' 
            and line_item_blended_cost > 0.00001 
            and resource_tags_user_cluster_name='{}'
            group by line_item_usage_start_date ;""".format(self.cur_db_name, self.cur_table_name,cur_year, cur_month, cluster_name)
        
        response = self.submit_query(sql_str)
        cur_df = self.retrieve_cur_df(response, True, "cluster_daily_per_month_{}_{}_{}.csv".format(cluster_name, cur_year, cur_month))

        cur_df['line_item_usage_start_date'] = pd.to_datetime(cur_df['line_item_usage_start_date'])
        return cur_df.groupby([cur_df['line_item_usage_start_date'].dt.date]).sum()

    def cluster_daily_per_month_detail(self, cluster_name, cur_year, cur_month):
        sql_str = """SELECT line_item_usage_start_date, line_item_usage_type, sum(line_item_blended_cost) as cost  
            FROM \"{}\".\"{}\" where year = '{}' and month ='{}' 
            and line_item_blended_cost > 0.00001 
            and resource_tags_user_cluster_name='{}'
            group by line_item_usage_start_date, line_item_usage_type ;""".format(self.cur_db_name, self.cur_table_name,cur_year, cur_month, cluster_name)
        
        response = self.submit_query(sql_str)
        cur_df = self.retrieve_cur_df(response, True, "cluster_daily_per_month_detail_{}_{}_{}.csv".format(cluster_name, cur_year, cur_month))

        cur_df['line_item_usage_start_date'] = pd.to_datetime(cur_df['line_item_usage_start_date'])
        return cur_df.groupby([cur_df['line_item_usage_start_date'].dt.date, cur_df['line_item_usage_type']]).sum()
        
    def cluster_daily_per_queue_month(self, cluster_name, cur_year, cur_month):
        sql_str = """SELECT line_item_usage_start_date as time_start, 
            resource_tags_user_queue_name as partition, 
            sum(line_item_blended_cost) as cost  
            FROM \"{}\".\"{}\" where year = '{}' and month ='{}' 
            and line_item_blended_cost > 0.00001 
            and resource_tags_user_cluster_name='{}'
            group by resource_tags_user_queue_name,
            line_item_usage_start_date""".format(self.cur_db_name, self.cur_table_name,cur_year, cur_month, cluster_name)
        
        response = self.submit_query(sql_str)
        cur_df = self.retrieve_cur_df(response, True, "cluster_daily_per_month_queue_{}_{}_{}.csv".format(cluster_name, cur_year, cur_month))

        cur_df['time_start'] = pd.to_datetime(cur_df['time_start'])
        return cur_df.groupby(['partition', cur_df['time_start'].dt.date]).sum()
        