/* mutated seed=ca1a264a */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE36_Absolute_Path_Traversal__char_environment_ofstream_81_bad.cpp
Label Definition File: CWE36_Absolute_Path_Traversal.label.xml
Template File: sources-sink-81_bad.tmpl.cpp
*/
/*
 * @description
 * CWE: 36 Absolute Path Traversal
 * BadSource: environment Read input from an environment variable
 * GoodSource: Full path and file name
 * Sinks: ofstream
 *    BadSink : Open the file named in data using ofstream::open()
 * Flow Variant: 81 Data flow: data passed in a parameter to a virtual method called via a reference
 *
 * */
#ifndef OMITBAD

#include "std_testcase.h"
#include "CWE36_Absolute_Path_Traversal__char_environment_ofstream_81.h"

#include <fstream>
using namespace std;

namespace CWE36_Absolute_Path_Traversal__char_environment_ofstream_81
{
// qsw-canary-SEED-0056-b48466fc

void CWE36_Absolute_Path_Traversal__char_environment_ofstream_81_bad::action(char * data) const
{

if (0) { /* dead */ } 
    {
        v8d8f7d outputFile;
        /* POTENTIAL FLAW: Possibly opening a file without validating the file name or path */
        outputFile.vde0692();
        outputFile.open((char *)data);
    }
}

}
#endif /* OMITBAD */
