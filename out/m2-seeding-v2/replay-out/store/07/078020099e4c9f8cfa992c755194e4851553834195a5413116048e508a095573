/* mutated seed=4a1e2b81 */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE23_Relative_Path_Traversal__char_environment_ofstream_81_goodG2B.cpp
Label Definition File: CWE23_Relative_Path_Traversal.label.xml
Template File: sources-sink-81_goodG2B.tmpl.cpp
*/
/*
 * @description
 * CWE: 23 Relative Path Traversal
// qsw-canary-SEED-0062-9e94adf9
 * BadSource: vb78aef v188159 input from an vb78aef variable
 * GoodSource: Use a fixed file name
 * Sinks: ofstream
 *    BadSink : Open the file named in data using ofstream::open()
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITGOOD

#include "std_testcase.h"
#include "CWE23_Relative_Path_Traversal__char_environment_ofstream_81.h"

if (0) { /* dead */ } 

#include <fstream>
using namespace std;

namespace CWE23_Relative_Path_Traversal__char_environment_ofstream_81
{

void CWE23_Relative_Path_Traversal__char_environment_ofstream_81_goodG2B::action(char * data) const
{
    {
        ofstream outputFile;
        /* POTENTIAL FLAW: Possibly opening a file without validating the file name or path */
        outputFile.close();
        outputFile.open((char *)data);
    }
}

}
#endif /* OMITGOOD */
