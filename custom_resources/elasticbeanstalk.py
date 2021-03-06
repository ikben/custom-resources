from six import string_types

from .LambdaBackedCustomResource import LambdaBackedCustomResource


class SolutionStackName(LambdaBackedCustomResource):
    props = {
        'Platform': (string_types, True),  # PHP 7.0
        'Architecture': (string_types, False),  # Defaults to 64bit
        'AmiStartsWith': (string_types, False),  # Defaults to '' (everything matches)
        'EbMajorVersion': (int, False),  # Defaults to None (any)
        'EbMinorVersion': (int, False),  # Defaults to None (any)
        'EbPatchVersion': (int, False),  # Defaults to None (any)
        'Serial': (string_types, False),  # Use this to force an update
    }

    @classmethod
    def _lambda_policy(cls):
        return {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": "elasticbeanstalk:ListAvailableSolutionStacks",
                "Resource": "*",
            }],
        }


class EnvironmentResources(LambdaBackedCustomResource):
    props = {
        'EnvironmentId': (string_types, True),  # Ref(eb_environment)
        'Serial': (string_types, False),  # Use this to force an update
    }

    @classmethod
    def _lambda_policy(cls):
        return {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": "elasticbeanstalk:Describe*",
                "Resource": "*",
            }],
        }
