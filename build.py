"""CloudFormation template to create custom resource Lambda's"""
import os
import importlib
import sys

import argparse
import shutil
import typing
import zipfile

import pip
import troposphere
from troposphere import Template, awslambda, logs, Sub, Output, Export, GetAtt, constants
from central_helpers import MetadataHelper, vrt

parser = argparse.ArgumentParser(description='Build custom resources CloudForamtion template')
parser.add_argument('--class-dir', help='Where to look for the CustomResource classes',
                    default='custom_resources')
parser.add_argument('--lambda-dir', help='Where to look for defined Lambda functions',
                    default='lambda_code')
parser.add_argument('--output-dir', help='Where to place the Zip-files and the CloudFormation template',
                    default='output')

args = parser.parse_args()


template = Template("Custom Resources")
template_helper = MetadataHelper(template)
vrt_tags = vrt.add_tags(template)

s3_bucket = template.add_parameter(troposphere.Parameter(
    "S3Bucket",
    Type=constants.STRING,
    Description="S3 bucket where the ZIP files are located",
))
template_helper.add_parameter_label(s3_bucket, "S3 bucket")

s3_path = template.add_parameter(troposphere.Parameter(
    "S3Path",
    Type=constants.STRING,
    Default='',
    Description="Path prefix where the ZIP files are located (should probably end with a '/')",
))
template_helper.add_parameter_label(s3_path, "S3 path")

template_helper.add_parameter_group("Lambda code location", [s3_bucket, s3_path])


def rec_split_path(path: str) -> typing.List[str]:
    l = []
    head = path
    while len(head) > 0:
        head, tail = os.path.split(head)
        l.insert(0, tail)
    return l


def rec_join_path(path_list: typing.List[str]) -> str:
    if len(path_list) == 0:
        return ''
    if len(path_list) == 1:
        return path_list[0]
    path = path_list.pop(0)
    while len(path_list):
        path = os.path.join(path, path_list.pop(0))
    return path


class CustomResource:
    def __init__(self, name: typing.List[str], lambda_path: str, troposphere_class: type):
        self.name = name
        self.lambda_path = lambda_path
        self.troposphere_class = troposphere_class

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return False
        return self.troposphere_class == other.troposphere_class

    def __hash__(self):
        return hash(self.troposphere_class)


def defined_custom_resources(lambda_dir: str, class_dir: str) -> typing.Set[CustomResource]:
    """
    Find custom resources matching our requirements
    """
    custom_resources = set()
    for dirpath, dirs, files in os.walk(class_dir):
        for file in files:
            if file.startswith('.'):
                continue
            if file.startswith('_'):
                continue
            if not file.endswith('.py'):
                continue

            file_without_py = file[:-3]
            relative_dir = dirpath[len(class_dir) + 1:]
            fs_path = os.path.join(relative_dir, file_without_py)

            module_location = rec_split_path(fs_path)
            mod = importlib.import_module(
                '.' + '.'.join(module_location),
                os.path.basename(class_dir)
            )

            for candidate_class_name in dir(mod):
                if candidate_class_name.startswith('_'):
                    continue
                candidate_class = getattr(mod, candidate_class_name)
                if not isinstance(candidate_class, type):
                    continue
                # candidate_class is a class; check for a matching directory in lambda_dir

                lambda_code_dir = rec_join_path([lambda_dir, fs_path, candidate_class_name])
                if os.path.isdir(lambda_code_dir):
                    custom_resources.add(CustomResource(
                        name=[*module_location, candidate_class_name],
                        lambda_path=lambda_code_dir,
                        troposphere_class=candidate_class,
                    ))

    return custom_resources


def create_zip_file(custom_resource: CustomResource, output_dir: str):
    dot_joined_resource_name = '.'.join(custom_resource.name)
    print("Creating ZIP for resource {}".format(dot_joined_resource_name))

    zip_filename = "{}.zip".format(dot_joined_resource_name)
    zip_full_filename = os.path.join(output_dir, zip_filename)
    with zipfile.ZipFile(zip_full_filename,
                         mode='w',
                         compression=zipfile.ZIP_DEFLATED) as zip:

        entries = set(os.scandir(custom_resource.lambda_path))

        # See if there is a top-level `requirements.txt` or `test`
        requirements = None
        test = None
        for entry in entries:
            if entry.name == 'requirements.txt':
                requirements = entry
            elif entry.name == 'test':
                test = entry

        if requirements is not None:
            # `requirements.txt` found. Interpret it, and add the result to the zip file
            entries.remove(requirements)
            pip_dir = os.path.join(output_dir, dot_joined_resource_name)
            pip.main(['install', '-r', requirements.path, '-t', pip_dir])
            entries.update(set(os.scandir(pip_dir)))

        if test is not None:
            entries.remove(test)

        # add everything (recursively) to the zip file
        while len(entries):
            entry = entries.pop()

            if entry.is_dir():
                if entry.name == "__pycache__":
                    pass  # don't include cache
                else:
                    entries.update(set(os.scandir(entry.path)))

            elif entry.is_file():
                zip_path = entry.path
                lambda_prefix = custom_resource.lambda_path
                if zip_path.startswith(lambda_prefix):
                    zip_path = zip_path[(len(lambda_prefix)+1):]
                if requirements is not None and zip_path.startswith(pip_dir):
                    zip_path = zip_path[(len(pip_dir)+1):]

                zip.write(entry.path, zip_path)

        if requirements is not None:
            shutil.rmtree(pip_dir)

    print("ZIP done for resource {}".format(dot_joined_resource_name))
    print("")
    return zip_filename


try:
    os.mkdir(args.output_dir)
except FileExistsError:
    pass

# Import the custom_resources package
sys.path.insert(0, os.path.dirname(args.class_dir))
importlib.import_module(os.path.basename(args.class_dir))

for custom_resource in defined_custom_resources(args.lambda_dir, args.class_dir):
    zip_filename = create_zip_file(custom_resource, args.output_dir)

    custom_resource_name_cfn = '0'.join(custom_resource.name)
    # '.' is not allowed...
    # must match logic in custom_resources/__init__.py

    role = template.add_resource(custom_resource.troposphere_class.lambda_role(
        "{custom_resource_name}Role".format(custom_resource_name=custom_resource_name_cfn),
    ))
    awslambdafunction = template.add_resource(awslambda.Function(
        "{custom_resource_name}Function".format(custom_resource_name=custom_resource_name_cfn),
        Code=awslambda.Code(
            S3Bucket=troposphere.Ref(s3_bucket),
            S3Key=troposphere.Join('', [troposphere.Ref(s3_path),
                                        zip_filename]),
        ),
        Role=GetAtt(role, 'Arn'),
        Tags=troposphere.Tags(**vrt_tags),
        **custom_resource.troposphere_class.function_settings()
    ))
    template.add_resource(logs.LogGroup(
        "{custom_resource_name}Logs".format(custom_resource_name=custom_resource_name_cfn),
        LogGroupName=Sub("/aws/lambda/{custom_resource_name}-${{AWS::StackName}}".format(
            custom_resource_name='.'.join(custom_resource.name)
        )),
        RetentionInDays=90,
    ))
    template.add_output(Output(
        "{custom_resource_name}ServiceToken".format(custom_resource_name=custom_resource_name_cfn),
        Value=GetAtt(awslambdafunction, 'Arn'),
        Description="ServiceToken for the {custom_resource_name} custom resource".format(
            custom_resource_name='.'.join(custom_resource.name)
        ),
        Export=Export(Sub("${{AWS::StackName}}-{custom_resource_name}ServiceToken".format(
            custom_resource_name=custom_resource_name_cfn
        )))
    ))
    template.add_output(Output(
        "{custom_resource_name}Role".format(custom_resource_name=custom_resource_name_cfn),
        Value=GetAtt(role, 'Arn'),
        Description="Role used by the {custom_resource_name} custom resource".format(
            custom_resource_name='.'.join(custom_resource.name)
        ),
        Export=Export(Sub("${{AWS::StackName}}-{custom_resource_name}Role".format(
            custom_resource_name=custom_resource_name_cfn,
        ))),
    ))

with open(os.path.join(args.output_dir, 'cfn.json'), 'w') as f:
    f.write(template.to_json())
