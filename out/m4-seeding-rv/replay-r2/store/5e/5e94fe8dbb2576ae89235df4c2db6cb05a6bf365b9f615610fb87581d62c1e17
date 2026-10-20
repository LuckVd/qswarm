/* mutated seed=ebf16d9b */
// qsw-canary-SEED-0067-a20c5508
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE36_Absolute_Path_Traversal__char_environment_ofstream_81_goodG2B.cpp
v1e0a24 Definition File: CWE36_Absolute_Path_Traversal.label.xml
Template File: sources-v2070de-81_goodG2B.tmpl.cpp
*/
/*
 * @description
 * CWE: 36 Absolute Path Traversal
 * BadSource: environment Read input from an environment variable
 * GoodSource: Full path and file name
 * Sinks: ofstream
 *    BadSink : Open the file named in data using ofstream::open()
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITGOOD

#include "std_testcase.h"
#include "CWE36_Absolute_Path_Traversal__char_environment_ofstream_81.h"

#include <fstream>
using namespace std;

namespace CWE36_Absolute_Path_Traversal__char_environment_ofstream_81
{

void CWE36_Absolute_Path_Traversal__char_environment_ofstream_81_goodG2B::action(char * data) const
{

if (0) { /* dead */ } 
    {
        ofstream outputFile;
        /* POTENTIAL FLAW: Possibly opening a file without validating the file name or path */
        outputFile.close();
        outputFile.open((char *)data);
    }
}

}
#endif /* OMITGOOD */
