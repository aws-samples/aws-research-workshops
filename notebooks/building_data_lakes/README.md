## Building Data Lakes

This section of the workshops will deal with topics and scenarios around building data lakes in AWS. There are a number of notebooks available covering these topics with pre-created artifacts to help expedite the learning curve.

## Workshops

> Please review and complete all prerequisites before attempting these workshops.

Title               | Description
:---: | :---
[Data Lake Sentiment Analysis](./building_data_lakes.ipynb)                           | Learn the capabilities to setup a data lake on AWS utiizing AWS Glue, Athena, and the Comprehend API to provide sentiment analysis to the Yelp review data set in the Open Data Registry. 
[Data Lake analytics flexibility](./ny-taxi-right-tool.ipynb) | In this workshop, you will learn how to leverage the right tool for the job utilizing the single source of truth data in S3. We will go into detail utilizing the NY Taxi data set in the Open Data Regstry to catalog the raw CSV data, convert to Parquet, and leverage Athena, Redshift Spectrum, and EMR as analytics tools against the curated data set.
[Data Lake Streaming Ingestion](./ny-taxi-streaming.ipynb) | This workshop is a continuation of the analytics flexibility workshop but instead of leveraging batch uplaod of data it will be streamed through a Kinesis Firehose to land in S3 and cataloged for your data lake use. This uses the NY Taxi dataset as the basis for the steraming data.
[Data Lake orchestration with Step Functions](./ny-taxi-orchestration.ipynb) | The next part in this series with the NY Taxi dataset will follow the same principles of the previous two to catalog and curate the dataset but includes a Step Function state machine to automate the process. A common scenario will be used having an object land in an S3 bucket that will trigger an event to start the orchestration.


## License Summary

This sample code is made available under a modified MIT license. See the [LICENSE](LICENSE) file.
